"""Trains a model, saving checkpoints and tensorboard summaries along
   the way."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

mode = "cifar_10"

from datetime import datetime
import json
import os
import shutil
import math
from timeit import default_timer as timer

import tensorflow as tf
import numpy as np
import sys

with open('config.json') as config_file:
    config = json.load(config_file)
    config_json = config

from model import Model
if config['data_path'] == 'cifar10_data':
  import cifar10_input
elif config['data_path'] == 'cifar100_data':
  import cifar100_input
from pgd_attack import LinfPGDAttack

from my_eval import my_eval

# seeding randomness
tf.set_random_seed(config['tf_random_seed'])
np.random.seed(config['np_random_seed'])

# Setting up training parameters
max_num_training_steps = config['max_num_training_steps']
num_output_steps = config['num_output_steps']
num_summary_steps = config['num_summary_steps']
num_checkpoint_steps = config['num_checkpoint_steps']
step_size_schedule = config['step_size_schedule']
weight_decay = config['weight_decay']
data_path = config['data_path']
momentum = config['momentum']
batch_size = config['training_batch_size']

# Setting up the data and the model
if config['data_path'] == 'cifar10_data':
  raw_cifar = cifar10_input.CIFAR10Data(data_path)#, bad_classes=config['pretrained_model_classes'])
elif config['data_path'] == 'cifar100_data':
  raw_cifar = cifar100_input.CIFAR100Data(data_path, bad_classes=config['pretrained_model_classes'])
global_step = tf.contrib.framework.get_or_create_global_step()
# model = Model(mode='train')
if config['data_path'] == 'cifar10_data':
  model = Model(mode='eval', class_count=10)
elif config['data_path'] == 'cifar100_data':
  model = Model(mode='eval', class_count=100)


# Setting up the optimizer
boundaries = [int(sss[0]) for sss in step_size_schedule]
boundaries = boundaries[1:]
values = [sss[1] for sss in step_size_schedule]
learning_rate = tf.train.piecewise_constant(
    tf.cast(global_step, tf.int32),
    boundaries,
    values)
total_loss = model.mean_xent + weight_decay * model.weight_decay_loss
# train_optimizer = tf.train.MomentumOptimizer(learning_rate, momentum)
# train_step = train_optimizer.minimize(
#     total_loss,
#     var_list=tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='logit'),
#     global_step=global_step)
with tf.variable_scope('optimizer'):
  train_optimizer = tf.train.AdamOptimizer()
  train_step = train_optimizer.minimize(
    total_loss,
    var_list=tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='logit'),
    global_step=global_step)

# Set up adversary
attack = LinfPGDAttack(model,
                       config['epsilon'],
                       config['num_steps'],
                       config['step_size'],
                       config['random_start'],
                       config['loss_func'])

# Setting up the Tensorboard and checkpoint outputs
model_dir = config['model_dir']
if not os.path.exists(model_dir):
  os.makedirs(model_dir)

# We add accuracy and xent twice so we can easily make three types of
# comparisons in Tensorboard:
# - train vs eval (for a single run)
# - train of different runs
# - eval of different runs

# saver = tf.train.Saver(max_to_keep=3)
train_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='logit')
optimizer_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='optimizer')
fixed_vars = [v for v in tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES) if v not in train_vars and v not in optimizer_vars]
saver = tf.train.Saver(max_to_keep=3, var_list=fixed_vars)
saver_for_saving = tf.train.Saver(max_to_keep=2)
tf.summary.scalar('accuracy adv train', model.accuracy)
tf.summary.scalar('accuracy adv', model.accuracy)
tf.summary.scalar('xent adv train', model.xent / batch_size)
tf.summary.scalar('xent adv', model.xent / batch_size)
tf.summary.image('images adv train', model.x_input)
merged_summaries = tf.summary.merge_all()

# keep the configuration file with the model for reproducibility
shutil.copy('config.json', model_dir)

config = tf.ConfigProto()
config.gpu_options.allow_growth = False
with tf.Session(config=config) as sess:

  # initialize data augmentation
  if config_json['data_path'] == 'cifar10_data':
    cifar = cifar10_input.AugmentedCIFAR10Data(raw_cifar, sess, model)
  elif config_json['data_path'] == 'cifar100_data':
    cifar = cifar100_input.AugmentedCIFAR100Data(raw_cifar, sess, model)

  # Initialize the summary writer, global variables, and our time counter.
  summary_writer = tf.summary.FileWriter(model_dir, sess.graph)
  
  # sess.run(tf.global_variables_initializer())
  saver.restore(sess, tf.train.latest_checkpoint(config_json['pretrained_model_dir']))
  sess.run([v.initializer for v in train_vars])
  sess.run([v.initializer for v in optimizer_vars])

  print('done loading model')

  training_time = 0.0

  # Main training loop
  for ii in range(1, max_num_training_steps+1):
    x_batch, y_batch = cifar.train_data.get_next_batch(batch_size,
                                                       multiple_passes=True)

    # Compute Adversarial Perturbations
    # start = timer()
    # x_batch_adv = attack.perturb(x_batch, y_batch, sess)
    # end = timer()
    # training_time += end - start

    nat_dict = {model.x_input: x_batch,
                model.y_input: y_batch}

    # adv_dict = {model.x_input: x_batch_adv,
    #             model.y_input: y_batch}

    # Output to stdout
    if ii % num_output_steps == 0:
      nat_acc = sess.run(model.accuracy, feed_dict=nat_dict)
      # adv_acc = sess.run(model.accuracy, feed_dict=adv_dict)
      print('Step {}:    ({})'.format(ii, datetime.now()))
      print('    training nat accuracy {:.4}%'.format(nat_acc * 100))
      sys.stdout.flush()
      # print('    training adv accuracy {:.4}%'.format(adv_acc * 100))
      # if ii != 0:
      #   print('    {} examples per second'.format(
      #       num_output_steps * batch_size / training_time))
      #   training_time = 0.0
    # Tensorboard summaries
    # if ii % num_summary_steps == 0:
      # summary = sess.run(merged_summaries, feed_dict=adv_dict)
      # summary_writer.add_summary(summary, global_step.eval(sess))

    # Write a checkpoint
    if ii % num_checkpoint_steps == 0 and ii != 0:
      saver_for_saving.save(sess,
                  os.path.join(model_dir, 'checkpoint'),
                  global_step=global_step)

      #======
#      print('Results of Eval Data:')
#      my_eval(config=config_json, cifar=raw_cifar, model=model, attack=attack, sess=sess, source='eval_data')
#      print('Results of Train Data:')
#      my_eval(config=config_json, cifar=raw_cifar, model=model, attack=attack, sess=sess, source='train_data')
      # num_eval_examples = config_json['num_eval_examples']
      # eval_batch_size = config_json['eval_batch_size']
      # num_batches = int(math.ceil(num_eval_examples / eval_batch_size))
      # total_xent_nat = 0.
      # total_xent_adv = 0.
      # total_corr_nat = 0
      # total_corr_adv = 0

      # for ibatch in range(num_batches):
      #   bstart = ibatch * eval_batch_size
      #   bend = min(bstart + eval_batch_size, num_eval_examples)

      #   x_batch = raw_cifar.eval_data.xs[bstart:bend, :]
      #   y_batch = raw_cifar.eval_data.ys[bstart:bend]

      #   dict_nat = {model.x_input: x_batch,
      #               model.y_input: y_batch}

      #   x_batch_adv = attack.perturb(x_batch, y_batch, sess)

      #   dict_adv = {model.x_input: x_batch_adv,
      #               model.y_input: y_batch}

      #   cur_corr_nat, cur_xent_nat = sess.run(
      #                                   [model.num_correct,model.xent],
      #                                   feed_dict = dict_nat)
      #   cur_corr_adv, cur_xent_adv = sess.run(
      #                                   [model.num_correct,model.xent],
      #                                   feed_dict = dict_adv)

      #   # print("batch {}/{} size: {}".format(ibatch, num_batches, eval_batch_size))
      #   # print("Correctly classified natural examples: {}".format(cur_corr_nat))
      #   # print("Correctly classified adversarial examples: {}".format(cur_corr_adv))
      #   total_xent_nat += cur_xent_nat
      #   total_xent_adv += cur_xent_adv
      #   total_corr_nat += cur_corr_nat
      #   total_corr_adv += cur_corr_adv

      # avg_xent_nat = total_xent_nat / num_eval_examples
      # avg_xent_adv = total_xent_adv / num_eval_examples
      # acc_nat = total_corr_nat / num_eval_examples
      # acc_adv = total_corr_adv / num_eval_examples


      # print('natural: {:.2f}%'.format(100 * acc_nat))
      # print('adversarial: {:.2f}%'.format(100 * acc_adv))
      # print('avg nat loss: {:.4f}'.format(avg_xent_nat))
      # print('avg adv loss: {:.4f}'.format(avg_xent_adv))

      #======

    # Actual training step
    start = timer()
    # sess.run(train_step, feed_dict=adv_dict)
    sess.run(train_step, feed_dict=nat_dict)
    end = timer()
    training_time += end - start
