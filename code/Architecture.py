import LearnTools
import Networks
from BatchMaker import *
import wandb
# import argparse
import os
import time
import random
import torch.nn as nn
import math
# import torch.nn.functional as F
# import torch.nn.parallel
# import torch.backends.cudnn as cudnn
import torch.optim as optim
import torch.utils.data
# import torchvision.datasets as dset
# import torchvision.transforms as transforms
# import torchvision.utils as vutils
from matplotlib import pyplot as plt
import argparse

if os.getcwd().endswith('code'):
    os.chdir('..')  # current directory from /SuperRes/code to SuperRes/

# Parsing arguments:
parser = argparse.ArgumentParser()

args = LearnTools.return_args(parser)

progress_dir, wd, wg = args.directory, args.widthD, args.widthG
n_res_blocks, pix_distance = args.n_res_blocks, args.pixel_coefficient_distance
num_epochs, g_update, n_dims = args.num_epochs, args.g_update, args.n_dims
g_update_start = args.g_update_start


if not os.path.exists(ImageTools.progress_dir + progress_dir):
    os.makedirs(ImageTools.progress_dir + progress_dir)

PATH_G = 'progress/' + progress_dir + '/g_weights.pth'
PATH_D = 'progress/' + progress_dir + '/d_weights.pth'
eta_file = 'eta.npy'

# G and D slices to choose from
g_slices = [0, 1]
d_slices = [0, 1]

# Root directory for dataset
dataroot = "data/"

# Number of workers for dataloader
workers = 2

# Batch sizes during training
if n_dims == 3:
    batch_size_G_for_D, batch_size_G, batch_size_D = 4, 32, 64
else:  # n_dims == 2
    batch_size_G_for_D, batch_size_G, batch_size_D = 64, 64, 64


# Number of channels in the training images. For color images this is 3
nc_g = 2  # two phases for the generator input
nc_d = 3  # three phases for the discriminator input

# number of iterations in each epoch
epoch_iterations = 10000//batch_size_G

# Learning rate for optimizers
lr = 0.0001

# Beta1 hyperparam for Adam optimizers
beta1 = 0.5

# Learning parameter for gradient penalty
Lambda = 10

# Number of GPUs available. Use 0 for CPU mode.
ngpu = 1

# When to save progress
saving_num = 50


# Decide which device we want to run on
device = torch.device(
    "cuda:0" if (torch.cuda.is_available() and ngpu > 0) else "cpu")
print('device is ' + str(device))

# the grey channel in the images:
grey_index = torch.LongTensor([1]).to(device)

# custom weights initialization called on netG and netD
def weights_init(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        nn.init.normal_(m.weight.data, 0.0, 0.02)
    elif classname.find('BatchNorm') != -1:
        nn.init.normal_(m.weight.data, 1.0, 0.02)
        nn.init.constant_(m.bias.data, 0)


def save_differences(network_g, high_res_im, grey_idx,
                     device, save_dir, filename, scale_factor, wandb):
    """
    Saves the image of the differences between the high-res real and the
    generated images that are supposed to be similar.
    """
    low_res_input = LearnTools.down_sample_for_g_input(high_res_im,
                                                       grey_idx,
                                                       scale_factor, device, n_dims)
    g_output = network_g(low_res_input).detach().cpu()
    ImageTools.plot_fake_difference(high_res_im.detach().cpu(),
                                    low_res_input.detach().cpu(), g_output,
                                    save_dir, filename, wandb)


def save_tif_3d(network_g, high_res_im, grey_idx, device, filename,
                mask=False):
    """
        Saves a tif image of the output of G on all of the 3d image high_res_im
    """
    high_res_length = high_res_im.size()[-1]
    scale_factor = network_g.return_scale_factor(high_res_length)
    low_res_input = LearnTools.down_sample_for_g_input(high_res_im,
                                                       grey_idx,
                                                       scale_factor, device, n_dims)
    print(low_res_input.size())
    network_g.train()
    g_output = network_g(low_res_input, mask).detach().cpu()
    # after_softmax = nn.Softmax(dim=1)(g_output)
    # without_mask = network_g(low_res_input, mask=False).detach().cpu()
    # difference = after_softmax - without_mask
    # change = np.abs(np.array(difference))
    # print(np.sum(change))
    # print(np.mean(change))
    # plt.hist(np.array(difference.view(-1)))
    # plt.show()
    # print('im here')
    # fig, a = plt.subplots(3, 2)
    # for i, title in enumerate(['Pore', 'Particle', 'Binder'], 0):
    #     print(i)
    #     a[i,0].hist(np.array(g_output[:, i, :, :, :].view(-1)), bins=150)
    #     a[i,0].set_xlim(-150,150)
    #     a[i,0].set_title(title + ' before softmax')
    #     a[i, 1].hist(np.array(after_softmax[:, i, :, :, :].view(-1)), bins=150)
    #     a[i, 1].set_xlim(0, 1)
    #     a[i, 1].set_title(title + ' after softmax')
    # plt.show()
    # for j, sup_title in enumerate(['Pore', 'Particle', 'Binder'], 0):
    #     output_mask = g_output[:, j, :, :, :].view(-1)>0
    #     fig, a = plt.subplots(3, 2)
    #     plt.suptitle('Voxels where ' + sup_title + ' is > 0')
    #     for i, title in enumerate(['Pore', 'Particle', 'Binder'], 0):
    #         print(i)
    #         a[i, 0].hist(np.array(g_output[:, i, :, :, :].view(
    #             -1)[output_mask]),
    #                      bins=150)
    #         a[i, 0].set_xlim(-150, 150)
    #         a[i, 0].set_title(title + ' before softmax')
    #         a[i, 1].hist(np.array(after_softmax[:, i, :, :, :].view(-1)[output_mask]), bins=150)
    #         a[i, 1].set_xlim(0, 1)
    #         a[i, 1].set_title(title + ' after softmax')
    #     plt.show()
    # print(low_res_input.size())
    # print(g_output.size())
    g_output = ImageTools.fractions_to_ohe(g_output)
    g_output_grey = ImageTools.one_hot_decoding(g_output).astype('uint8')
    # g_output_mask = ImageTools.fractions_to_ohe(without_mask)
    # g_output_grey_mask = ImageTools.one_hot_decoding(g_output).astype('uint8')
    # difference = g_output_grey - g_output_grey_mask
    # change = np.abs(np.array(difference))
    # print(np.sum(change))
    # print(np.mean(change))
    imsave('progress/' + progress_dir + '/' + filename, g_output_grey)
    low_res_grey = ImageTools.one_hot_decoding(low_res_input).astype('uint8')
    imsave('progress/' + progress_dir + '/low_res' + filename, low_res_grey)
    high_res_im = ImageTools.one_hot_decoding(high_res_im).astype('uint8')
    imsave('progress/' + progress_dir + '/' + filename + '-original',
           high_res_im)


if __name__ == '__main__':

    # 1. Start a new run
    wandb.init(project='3d to 3d', config=args, name=progress_dir)

    # The batch maker:
    BM = BatchMaker(device, dims=n_dims)

    # Create the generator
    netG = Networks.generator(ngpu, wg, nc_g, nc_d, n_res_blocks, n_dims).to(
        device)
    wandb.watch(netG, log='all')

    # Handle multi-gpu if desired
    if (device.type == 'cuda') and (ngpu > 1):
        netG = nn.DataParallel(netG, list(range(ngpu)))

    # Create the Discriminator
    netD = Networks.discriminator(ngpu, wd, nc_d, n_dims).to(device)

    # Handle multi-gpu if desired
    if (device.type == 'cuda') and (ngpu > 1):
        netD = nn.DataParallel(netD, list(range(ngpu)))

    # Apply the weights_init function to randomly initialize all weights
    #  to mean=0, stdev=0.2.
    # netG.apply(weights_init)
    # netD.apply(weights_init)

    # Setup Adam optimizers for both G and D
    optimizerD = optim.Adam(netD.parameters(), lr=lr, betas=(beta1, 0.999))
    optimizerG = optim.Adam(netG.parameters(), lr=lr, betas=(beta1, 0.999))

    def generate_fake_image(detach_output=True):
        """
        :param detach_output: to detach the tensor output from gradient memory.
        :return: the generated image from G
        """
        # Generate batch of g input
        g_slice = random.choice(g_slices)
        before_down_sampling = BM.random_batch_for_fake(batch_size_G_for_D,
                                                        g_slice)
        # down sample:
        low_res_im = LearnTools.down_sample_for_g_input(
            before_down_sampling, grey_index, BM.train_scale_factor, device, n_dims)

        # Generate fake image batch with G
        if detach_output:
            return low_res_im, netG(low_res_im).detach()
        else:
            return low_res_im, netG(low_res_im)

    def take_fake_slices(fake_image, perm_idx):
        """
        :return: batch of slices from the 3d image (if 2d image,
        just returns the image)
        """
        if n_dims == 3:
            perm = perms_3d[perm_idx]
            # permute the fake output of G to make it into a batch
            # of images to feed D (each time different axis)
            fake_slices = fake_image.permute(0, perm[0], 1, *perm[1:])
            # the new batch size feeding D:
            batch_size_new = batch_size_G_for_D * BM.high_l
            # reshaping for the correct size of D's input
            return fake_slices.reshape(batch_size_new, nc_d,
                                              BM.high_l, BM.high_l)
        else:  # same 2d slices are fed into D
            return fake_image

    # Training Loop!
    steps = epoch_iterations
    print("Starting Training Loop...")
    start = time.time()

    for epoch in range(num_epochs):
        # For each batch in the dataloader
        i = 0

        j = np.random.randint(steps)  # to see different slices
        for _ in range(steps):

            ############################
            # (1) Update D network:
            ###########################

            _, fake_for_d = generate_fake_image(detach_output=True)

            for k in range(math.comb(n_dims, 2)):

                # Train with all-real batch
                netD.zero_grad()
                # Format batch
                d_slice = random.choice(d_slices)
                high_res = BM.random_batch_for_real(batch_size_D, d_slice)

                # Forward pass real batch through D
                output_real = netD(high_res).view(-1).mean()

                # obtain fake slices from the fake image
                fake_slices = take_fake_slices(fake_for_d, k)

                # Classify all fake batch with D
                output_fake = netD(fake_slices).view(-1).mean()

                # Calculate gradient penalty
                gradient_penalty = LearnTools.calc_gradient_penalty(netD,
                                   high_res, fake_slices[:batch_size_D],
                                   batch_size_D, BM.high_l, device,
                                   Lambda, nc_d)

                # discriminator is trying to minimize:
                d_cost = output_fake - output_real + gradient_penalty
                # Calculate gradients for D in backward pass
                d_cost.backward()
                optimizerD.step()

                wass = abs(output_fake.item() - output_real.item())

            ############################
            # (2) Update G network:
            ###########################

            if (i % (g_update + g_update_start)) == 0:
                netG.zero_grad()
                # generate fake again to update G:
                low_res, fake_for_g = generate_fake_image(detach_output=False)
                # save the cost of g to add from each axis:
                g_cost = 0
                # go through each axis
                for k in range(math.comb(n_dims, 2)):
                    fake_slices = take_fake_slices(fake_for_g, k)
                    # perform a forward pass of all-fake batch through D
                    fake_output = netD(fake_slices).view(-1)
                    # get the pixel-wise-distance loss
                    pix_loss = LearnTools.pixel_wise_distance(low_res,
                               fake_for_g, grey_index, BM.train_scale_factor,
                                                              n_dims)
                    # Calculate G's loss based on this output
                    if pix_loss.item() > 0.1:
                        g_cost += -fake_output.mean() + pix_distance * pix_loss
                    else:
                        g_cost += -fake_output.mean()

                # Calculate gradients for G
                g_cost.backward()
                # Update G
                optimizerG.step()
                wandb.log({"pixel distance": pix_loss})
                wandb.log({"wass": wass})
                wandb.log({"real": output_real, "fake": output_fake})

            # Output training stats
            if i == j:
                ImageTools.calc_and_save_eta(steps, time.time(), start, i,
                                             epoch, num_epochs, eta_file)

                with torch.no_grad():  # only for plotting
                    save_differences(netG, BM.random_batch_for_fake(
                                     6, random.choice(g_slices)).detach(),
                                     grey_index, device, progress_dir,
                                     'running slices', BM.train_scale_factor,
                                     wandb)
            i += 1

        if g_update_start > 0:
            g_update_start -= 1

        if (epoch % 3) == 0:
            torch.save(netG.state_dict(), PATH_G)
            torch.save(netD.state_dict(), PATH_D)
            wandb.save(PATH_G)
            wandb.save(PATH_D)

    print('finished training')
