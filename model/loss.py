import torch
from torch import nn

import tools


def smooth_l1_loss(input, target, beta=1/9):
    """
    very similar to the smooth_l1_loss from pytorch, but with
    the extra beta parameter
    """
    n = torch.abs(input - target)
    cond = n < beta
    loss = torch.where(cond, 0.5 * n ** 2 / beta, n - 0.5 * beta)
    return loss.mean(dim=-1, keepdim=True)

def focal(target, actual, alpha=0.5, gamma=2):
    alpha_t = 2 * torch.abs(target - 1 + alpha)
    focal = alpha_t * torch.pow(torch.abs(target - actual), gamma)
    return focal

def loss_per_scale(pred, label, bboxes, opt):
    # type: (torch.Tensor, torch.Tensor, torch.Tensor, dict) -> Tuple[torch.Tensor]
    stride = opt['stride']
    ignore_thresh = opt['ignore_thresh']
    bbox_loss_type = opt['bbox_loss']

    bce_loss = nn.BCELoss(reduction='none')
    out_size_h, out_size_w = pred.shape[1:3]
    in_size = (stride * out_size_h, stride * out_size_w)

    pred_coor = pred[..., 0:4]
    pred_conf = pred[..., 4:5]
    pred_prob = pred[..., 5:]

    label_coor = label[..., 0:4]
    respond_bbox = label[..., 4:5]
    label_prob = label[..., 5:-1]
    label_mixw = label[..., -1:]

    bbox_wh = label_coor[..., 2:] - label_coor[..., :2]
    bbox_loss_scale = 2.0 - 1.0 * \
        bbox_wh[..., 0:1] * bbox_wh[..., 1:2] / (in_size[0] * in_size[1])

    # bbox loss
    if bbox_loss_type == 'l1':
        bbox_loss = respond_bbox * bbox_loss_scale *\
            smooth_l1_loss(pred_coor, label_coor) * opt['l1_loss_gain']
    elif bbox_loss_type == 'giou':
        giou = tools.giou(pred_coor, label_coor)
        giou = giou[..., None]
        bbox_loss = respond_bbox * bbox_loss_scale * (1.0 - giou)
    elif bbox_loss_type == 'iou':
        iou = tools.iou_calc3(pred_coor, label_coor)
        iou = iou[..., None]
        bbox_loss = respond_bbox * bbox_loss_scale * (1.0 - iou)
    else:
        raise NotImplementedError

    # confidence loss
    iou = tools.iou_calc3(pred_coor[:, :, :, :, None, :],
                            bboxes[:, None, None, None, :, :])
    max_iou, _ = torch.max(iou, -1)
    max_iou = max_iou[..., None]
    respond_bgd = (1.0 - respond_bbox) * \
        (max_iou < ignore_thresh).float()

    conf_focal = focal(respond_bbox, pred_conf, alpha=0.5)

    conf_loss = conf_focal * (
        respond_bbox * bce_loss(pred_conf, respond_bbox)
        +
        respond_bgd * bce_loss(pred_conf, respond_bbox)
    )

    # classes loss
    # class_focal = 2 * focal(label_prob, pred_prob, alpha=0.5)
    prob_loss = respond_bbox * bce_loss(pred_prob, label_prob)

    # sum up
    bbox_loss = (bbox_loss * label_mixw).sum([1, 2, 3, 4]).mean()
    conf_loss = (conf_loss * label_mixw).sum([1, 2, 3, 4]).mean()
    prob_loss = (prob_loss * label_mixw).sum([1, 2, 3, 4]).mean()
    loss = bbox_loss + conf_loss + prob_loss

    if torch.isnan(loss):
        print('xy: {}, conf: {}, cls: {}'.format(bbox_loss, conf_loss, prob_loss))
        raise RuntimeError('NaN in loss')
    return loss, bbox_loss, conf_loss, prob_loss
