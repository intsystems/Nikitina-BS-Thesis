import typing

import numpy as np
import torch
import torch.nn as nn


def get_negative_mask(batch_size: int) -> torch.Tensor:
    """
    Шаблон для маски с нулями на главной диагонали и на двух
    параллельных ей
    """
    negative_mask = torch.ones((batch_size, 2 * batch_size), dtype=bool)
    for i in range(batch_size):
        negative_mask[i, i] = 0
        negative_mask[i, i + batch_size] = 0

    negative_mask = torch.cat((negative_mask, negative_mask), 0)
    return negative_mask


def get_target_mask(target: torch.Tensor) -> torch.Tensor:
    """
    Маска для элементов одного класса
    """
    target_ = torch.cat([target, target], dim=0).view(-1, 1)
    mask = (target_ == target_.t().contiguous()) & (target_ != -1)
    return mask


def replace_pos_diagonals(full_matrix, pos_m):
    """
    Удаление положительный саб-диагоналей
    """
    batch_size = full_matrix.shape[0] // 2

    new_matrix = full_matrix.clone()

    new_matrix[range(batch_size), range(batch_size, 2*batch_size)] = pos_m[:batch_size]
    new_matrix[range(batch_size, 2*batch_size), range(batch_size)] = pos_m[batch_size:]
    return new_matrix


class ContrastiveLossBase(nn.Module):
    def __init__(self, temperature, cuda):
        super().__init__()
        self.temperature: float = temperature
        self.cuda: bool = cuda

    def forward(self, out_1, out_2, target):
        batch_size = out_1.shape[0]
        
        out = torch.cat([out_1, out_2], dim=0)
        # Скалярное произведение
        full = torch.exp(torch.mm(out, out.t().contiguous()) / self.temperature)

        # Негативные пары
        mask = get_negative_mask(batch_size)
        if self.cuda:
            mask = mask.cuda()
        neg = full.masked_select(mask).view(2 * batch_size, -1)
        
        # Скалярное произведение двух позитивных элементов
        pos = torch.exp(torch.sum(out_1 * out_2, dim=-1) / self.temperature)
        pos = torch.cat([pos, pos], dim=0)

        return pos, neg

class ContrastiveLoss(ContrastiveLossBase):
    def forward(self, out_1, out_2, out_m, target):
        pos, neg = super().forward(out_1, out_2, target)
        Ng = neg.sum(dim=-1)
        loss = (-torch.log(pos / (pos + Ng))).mean()
        return loss


class DebiasedNegLoss(ContrastiveLossBase):
    def __init__(self, temperature, cuda, tau_plus):
        super().__init__(temperature, cuda)
        self.tau_plus = tau_plus

    def forward(self, out_1, out_2, out_m, target):
        pos, neg = super().forward(out_1, out_2, target)
        batch_size = out_1.shape[0]

        pos_m = [pos]
        pos_m = torch.stack(pos_m, dim=0).mean(dim=0)

        N = batch_size * 2 - 2
        Ng = (-self.tau_plus * N * pos_m + neg.sum(dim=-1)) / (1 - self.tau_plus)
        # constrain (optional)
        Ng = torch.clamp(Ng, min=N * np.e ** (-1 / self.temperature))
        loss = (-torch.log(pos / (pos + Ng))).mean() # contrastive loss
        return loss

    def extra_repr(self):
        return "Tau plus: {}".format(self.tau_plus)


class DebiasedPosLoss(nn.Module):
    def __init__(self, temperature, cuda, tau_plus):
        super().__init__()
        self.temperature: float = temperature
        self.cuda: bool = cuda
        self.tau_plus: float = tau_plus

    def forward(self, out_1, out_2, out_m, target):
        batch_size = out_1.shape[0]
        N = batch_size * 2 - 2

        out = torch.cat([out_1, out_2], dim=0)
        full = torch.exp(torch.mm(out, out.t().contiguous()) / self.temperature)

        # Позитивные классы
        pos = torch.exp(torch.sum(out_1 * out_2, dim=-1) / self.temperature)
        pos = torch.cat([pos, pos], dim=0)

        full = replace_pos_diagonals(full)
        p_estimate = full.mean(dim=-1)

        # Негативные классы
        neg_mask = get_negative_mask(batch_size)
        if self.cuda:
            neg_mask = neg_mask.cuda()
        neg = full.masked_select(neg_mask).view(2 * batch_size, -1)

        # Вычисление лосс-функции
        tau_minus = 1 - self.tau_plus
        g = neg.mean(dim=-1)
        numerator = p_estimate - tau_minus * g
        denominator = p_estimate + (N * self.tau_plus - tau_minus) * g
        loss = (-torch.log(numerator / denominator)).mean()

        return loss

def get_loss(
    name: str,
    temperature: float,
    cuda: bool,
    tau_plus: float,
) -> nn.Module:

    if name == "Contrastive":
        return ContrastiveLoss(temperature, cuda)
    if name == "DebiasedNeg":
        return DebiasedNegLoss(temperature, cuda, tau_plus)
    if name == "DebiasedPos":
        return DebiasedPosLoss(temperature, cuda, tau_plus)
    raise Exception("Неправильный лосс {}".format(name))
