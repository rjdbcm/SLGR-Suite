from warnings import warn
from beagles.backend.net.ops.baseop import BaseOp, BaseOpV2
from deprecated.sphinx import deprecated
import tensorflow as tf
import numpy as np

class Reorg(tf.keras.layers.Layer):
    def __init__(self, layer):
        super(Reorg, self).__init__()
        self.lay = layer

    def call(self, inputs):
        s = self.lay.stride
        return tf.image.extract_patches(inputs, [1,s,s,1], [1,s,s,1], [1,1,1,1], 'VALID')


class reorg(BaseOp):
    def _forward(self):
        inp = self.inp.out
        shape = inp.get_shape().as_list()
        _, h, w, c = shape
        s = self.lay.stride
        out = list()
        for i in range(int(h/s)):
            row_i = list()
            for j in range(int(w/s)):
                si, sj = s * i, s * j
                boxij = inp[:, si: si+s, sj: sj+s,:]
                flatij = tf.reshape(boxij, [-1,1,1,c*s*s])
                row_i += [flatij]
            out += [tf.concat(row_i, 2)]

        self.out = tf.concat(out, 1)

    def forward(self):
        inp = self.inp.out
        s = self.lay.stride
        self.out = tf.image.extract_patches(inp, [1,s,s,1], [1,s,s,1], [1,1,1,1], 'VALID')

    def speak(self):
        args = [self.lay.stride] * 2
        msg = 'local flatten {}x{}'
        return msg.format(*args)

class Local(BaseOpV2):
    def __init__(self, *args):
        super(Local, self).__init__(*args)

    def build(self, input_shape):
        ksz = (self.lay.ksize,) * 2
        filt = self.lay.wshape['kernels'][-1]
        stride = (self.lay.stride,) * 2
        self._lay = tf.keras.layers.LocallyConnected2D(filt, ksz, stride,
                                                       trainable=True, name=self.scope)

    def call(self, inputs, **kwargs):
        pad = [[self.lay.pad, self.lay.pad]] * 2
        temp = tf.pad(inputs, [[0, 0]] + pad + [[0, 0]])
        return self._lay(temp)


class local(BaseOp):
    def forward(self):
        pad = [[self.lay.pad, self.lay.pad]] * 2
        temp = tf.pad(self.inp.out, [[0, 0]] + pad + [[0, 0]])

        k = self.lay.w['kernels']
        ksz = self.lay.ksize
        half = int(ksz / 2)
        out = list()
        for i in range(self.lay.h_out):
            row_i = list()
            for j in range(self.lay.w_out):
                kij = k[i * self.lay.w_out + j]
                i_, j_ = i + 1 - half, j + 1 - half
                tij = temp[:, i_ : i_ + ksz, j_ : j_ + ksz,:]
                row_i.append(
                    tf.nn.conv2d(tij, kij, 
                        padding = 'VALID', 
                        strides = [1] * 4))
            out += [tf.concat(row_i, 2)]

        self.out = tf.concat(out, 1)

    def speak(self):
        l = self.lay
        args = [l.ksize] * 2 + [l.pad] + [l.stride]
        args += [l.activation]
        msg = 'loca {}x{}p{}_{}  {}'.format(*args)
        return msg


class Convolutional(BaseOpV2):
    def __init__(self, *args, **kwargs):
        super(Convolutional,self).__init__(*args, **kwargs)

    def build(self, input_shape):
        self.b = self.add_weight(
            shape=tuple(self.lay.wshape['biases']),
            initializer="random_normal",
            trainable=True,
            name=f'{self.scope}-bias'
        )

    def call(self, inputs, **kwargs):
        pad = [[self.lay.pad, self.lay.pad]] * 2
        temp = tf.pad(inputs, [[0, 0]] + pad + [[0, 0]])
        self.kw = self.add_weight(shape=tuple(self.lay.wshape['kernel']), dtype=tf.float32,
                                  name=f'{self.scope}-kweight' )
        temp = tf.nn.conv2d(temp, self.kw, padding='VALID',
                            name=self.scope, strides=[1] + [self.lay.stride] * 2 + [1])
        if self.lay.batch_norm:
            temp = self.batchnorm(temp)
        return tf.nn.bias_add(temp, self.b)

    def batchnorm(self, inputs):
        if not self.var:
            temp = (inputs - self.lay.w['moving_mean'])
            temp /= (np.sqrt(self.lay.w['moving_variance']) + 1e-5)
            temp *= self.lay.w['gamma']
            return temp
        else:
            args = dict({
                'center': False,
                'scale': True,
                'epsilon': 1e-5,
                'name': self.scope,
            })
            return tf.keras.layers.BatchNormalization(**args)(inputs)


class convolutional(BaseOp):
    def forward(self):
        pad = [[self.lay.pad, self.lay.pad]] * 2
        temp = tf.pad(self.inp.out, [[0, 0]] + pad + [[0, 0]])
        dtype = temp.dtype
        # import sys
        # print(self.lay.w['kernel'], self.lay.ksize, file=sys.stderr)
        # temp = tf.keras.layers.Conv2D(self.lay.filters, self.lay.ksize, padding='VALID', name=self.scope, strides=tuple([self.lay.stride]*2))(temp)
        temp = tf.nn.conv2d(temp, tf.cast(self.lay.w['kernel'], dtype), padding = 'VALID',
            name = self.scope, strides = [1] + [self.lay.stride] * 2 + [1])
        if self.lay.batch_norm:
            temp = self.batchnorm(self.lay, temp)
        self.out = tf.nn.bias_add(temp, tf.cast(self.lay.w['biases'], dtype))

    def batchnorm(self, layer, inp):
        if not self.var:
            temp = (inp - layer.w['moving_mean'])
            temp /= (np.sqrt(layer.w['moving_variance']) + 1e-5)
            temp *= layer.w['gamma']
            return temp
        else:
            args = dict({
                'center': False,
                'scale': True,
                'epsilon': 1e-5,
                'name': self.scope,
                })
            return tf.keras.layers.BatchNormalization(**args)(inp)

    def speak(self):
        msg = 'conv {}x{}p{}_{}  {}  {}'.format(*self.get_args())
        return msg

    def get_args(self):
        l = self.lay
        args = [l.ksize] * 2 + [l.pad] + [l.stride]
        args += [l.batch_norm * '+bnorm']
        args += [l.activation]
        return args

@deprecated(reason='DEPRECATION', version="1.0.0a1")
class conv_select(convolutional):
    def speak(self):
        msg = 'sele {}x{}p{}_{}  {}  {}'.format(*self.get_args())
        return msg

@deprecated(reason='DEPRECATION', version="1.0.0a1")
class conv_extract(convolutional):
    def speak(self):
        msg = 'extr {}x{}p{}_{}  {}  {}'.format(*self.get_args())
        return msg