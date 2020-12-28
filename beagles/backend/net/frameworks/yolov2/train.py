import tensorflow as tf
from tensorflow.python.framework.errors_impl import InvalidArgumentError
import numpy as np
from scipy.special import expit
try:
    import tensorflow.experimental.numpy as tnp
    TF_NUMPY = True
except AttributeError:
    TF_NUMPY = False

_np = np
np = tnp if TF_NUMPY else np

def loss(self, y_pred, _probs, _confs, _coord, _proid, _areas, _upleft, _botright):

    """
    Takes net.out and placeholders value
    returned in batch() func above,
    to build train_op and loss
    """
    m = self.meta
    sprob = float(m['class_scale'])
    sconf = float(m['object_scale'])
    snoob = float(m['noobject_scale'])
    scoor = float(m['coord_scale'])
    H, W, _ = m['out_size']
    B, C = m['num'], m['classes']
    HW = H * W  # number of grid cells
    anchors = m['anchors']
    if self.first:
        # log metadata
        self.logger.info(f'{m["model"]} loss hyper-parameters:')
        self.logger.info(f'\tH       = {H}')
        self.logger.info(f'\tW       = {W}')
        self.logger.info(f'\tbox     = {m["num"]}')
        self.logger.info(f'\tclasses = {m["classes"]}')
        self.logger.info(f'\tscales  = {[sprob, sconf, snoob, scoor]}')
        # Anchors logged as a list of ordered pairs for readability
        self.logger.info(f'\tanchors = {list(zip(*[iter(anchors)]*2))}')
        self.first = False
    # Extract the coordinate prediction from net.out
    try:
        net_out_reshape = tf.reshape(y_pred, [-1, H, W, B, (4 + 1 + C)])
    except InvalidArgumentError as e:
        msg = "\nCheck your model configuration file."
        self.flags.error = str(e) + msg
        self.logger.error(str(e))
        self.flags.kill = True
        self.io.send_flags()
        raise RuntimeError(self.flags.error)
    coords = net_out_reshape[:, :, :, :, :4]
    coords = tf.reshape(coords, [-1, H*W, B, 4])
    adjusted_coords_xy = expit(coords[:, :, :, 0:2])
    adjusted_coords_wh = tf.math.sqrt(tf.math.exp(coords[:, :, :, 2:4]) * np.reshape(anchors, [1, 1, B, 2]) / np.reshape([W, H], [1, 1, 1, 2]))
    coords = tf.concat([adjusted_coords_xy, adjusted_coords_wh], 3)

    adjusted_c = expit(net_out_reshape[:, :, :, :, 4])
    adjusted_c = tf.reshape(adjusted_c, [-1, H*W, B, 1])

    adjusted_prob = tf.math.softmax(net_out_reshape[:, :, :, :, 5:])
    adjusted_prob = tf.reshape(adjusted_prob, [-1, H*W, B, C])

    adjusted_net_out = tf.concat([adjusted_coords_xy, adjusted_coords_wh, adjusted_c, adjusted_prob], 3)

    wh = tf.math.pow(coords[:, :, :, 2:4], 2) * np.reshape([W, H], [1, 1, 1, 2])
    area_pred = wh[:, :, :, 0] * wh[:, :, :, 1]
    centers = coords[:, :, :, 0:2]
    floor = centers - (wh * .5)
    ceil  = centers + (wh * .5)

    # calculate the intersection areas
    intersect_upleft   = tf.math.maximum(floor, _upleft)
    intersect_botright = tf.math.minimum(ceil, _botright)
    intersect_wh = intersect_botright - intersect_upleft
    intersect_wh = tf.math.maximum(intersect_wh, 0.0)
    intersect = tf.math.multiply(intersect_wh[:, :, :, 0], intersect_wh[:, :, :, 1])

    # calculate the best IOU, set 0.0 confidence for worse boxes
    iou = tf.math.truediv(intersect, _areas + area_pred - intersect)
    best_box = tf.math.equal(iou, tf.math.reduce_max(iou, [2], True))
    best_box = tf.cast(best_box, tf.float32)
    confs = tf.math.multiply(best_box, _confs)

    # take care of the weight terms
    conid = snoob * (1. - confs) + sconf * confs
    weight_coo = tf.concat(4 * [tf.expand_dims(confs, -1)], 3)
    cooid = scoor * weight_coo
    weight_pro = tf.concat(C * [tf.expand_dims(confs, -1)], 3)
    proid = sprob * weight_pro

    self.fetch += [_probs, confs, conid, cooid, proid]
    true = tf.concat([_coord, tf.expand_dims(confs, 3), _probs], 3)
    wght = tf.concat([cooid, tf.expand_dims(conid, 3), proid], 3)

    loss = tf.math.pow(adjusted_net_out - true, 2)
    loss = tf.math.multiply(loss, wght)
    loss = tf.reshape(loss, [-1, H*W*B*(4 + 1 + C)])
    loss = tf.math.reduce_sum(loss, 1)
    return .5 * tf.math.reduce_mean(loss)