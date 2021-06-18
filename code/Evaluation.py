import BatchMaker
import LearnTools
import Networks
import ImageTools
import argparse
import torch
import numpy as np
from tifffile import imsave, imread
from torch.nn.functional import interpolate
import wandb

# Parsing arguments:
parser = argparse.ArgumentParser()

args = LearnTools.return_args(parser)

progress_dir, wd, wg = args.directory, args.widthD, args.widthG
n_res_blocks, pix_distance = args.n_res_blocks, args.pixel_coefficient_distance
num_epochs, g_update, n_dims = args.num_epochs, args.g_update, args.n_dims
squash = args.squash_phases
D_dimensions_to_check, scale_f = args.d_dimensions_to_check, args.scale_factor

phases_to_low = [1]
down_sample = False

progress_main_dir = 'progress/' + progress_dir
# progress_main_dir = 'progress'
path_to_g_weights = progress_main_dir + '/g_weights.pth'
# path_to_g_weights = progress_main_dir + '/g_weights_large_slice.pth'
G_image_path = 'data/separator_wo_fibrils.tif'
# G_image_path = 'data/new_vol_down_sample.tif'

file_name = 'generated_tif.tif'
crop_to_cube = False
down_sample_without_memory = True

# TODO all of these (ngpu, device, to_low_idx, nc_g..) can go into a
#  function in LearnTools that Architecture can also use
# Number of GPUs available. Use 0 for CPU mode.
ngpu = 1

# Decide which device we want to run on
device = torch.device(
    "cuda:0" if (torch.cuda.is_available() and ngpu > 0) else "cpu")
print('device is ' + str(device))

# the material indices to low-res:
to_low_idx = torch.LongTensor(phases_to_low).to(device)

# Number of channels in the training images. For color images this is 3
if squash:
    nc_g = 2
else:
    nc_g = 1 + to_low_idx.size()[0]  # channel for pore plus number of
    # material phases to low res.
nc_d = 3  # three phases for the discriminator input



G_net = Networks.generator(ngpu, wg, nc_g, nc_d, n_res_blocks, n_dims,
                           scale_factor=scale_f).to(device)
G_net.load_state_dict(torch.load(path_to_g_weights, map_location=torch.device(
    device)))
G_net.eval()

def cut_to_fit_up_sample(image, step_length, step_size):
    reminder = (np.array(image.shape) - step_length) % step_size
    return image[..., reminder[0]:,reminder[1]:,reminder[2]:]

def down_sample_wo_memory(path, step_length, step_size):
    high_res_vol = imread(path)
    high_res_vol = cut_to_fit_up_sample(high_res_vol, step_length, step_size)
    ohe_hr_vol = ImageTools.one_hot_encoding(high_res_vol,
                                             np.unique(high_res_vol))
    ohe_hr_vol = np.expand_dims(ohe_hr_vol, axis=0)
    material_phases = torch.index_select(torch.tensor(ohe_hr_vol), 1,
                                         to_low_idx)
    mat_phase_double = material_phases.double()
    mat_low_res = interpolate(mat_phase_double, scale_factor=1 / scale_f,
                              mode='trilinear')
    mat_low_res += (torch.rand(mat_low_res.size()) - 0.5) / 100
    mat_low_res = torch.where(mat_low_res > 0.5, 1., 0.)
    pore_phase = torch.ones(size=mat_low_res.size(),
                            device=device) - mat_low_res
    return torch.cat((pore_phase, mat_low_res), dim=1)



with torch.no_grad():  # save the images
    # 1. Start a new run
    wandb.init(project='SuperRes', name='making large volume',
               entity='tldr-group')

    step_len = 32
    overlap = 16
    high_overlap = int(overlap / 2 * scale_f)
    step = step_len - overlap

    if down_sample_without_memory:
        im_3d = down_sample_wo_memory(path=G_image_path,
                                      step_length=step_len, step_size=step)
    else:
        BM_G = BatchMaker.BatchMaker(path=G_image_path, device=device,
                                     to_low_idx=to_low_idx, rot_and_mir=False)
        im_3d = BM_G.all_image_batch()

    # orig_im_3d = BM_D.all_image_batch()
    # if crop_to_cube:
    #     min_d = 128
    #     im_3d = im_3d[:, :, :min_d, :min_d, :min_d]
        # orig_im_3d = orig_im_3d[:, :, :min_d, :min_d, :min_d]
    # save_tif_3d(G_net, im_3d, to_low_idx, device, file_name)
    nz1, nz2, nz3 = im_3d.size()[-3:].to(device)

    first_img_stack = []
    with torch.no_grad():
        last_ind1 = int((nz1-step_len)//step)
        for i in range(last_ind1 + 1):
            wandb.log({'large step': i})
            print('i = ' + str(i))
            first_lr_vec = im_3d[..., i*step:i*step+step_len, :, :]
            second_img_stack = []
            last_ind2 = int((nz2-step_len)//step)
            for j in range(last_ind2 + 1):
                print(j)
                second_lr_vec = first_lr_vec[..., :, j * step:j * step +
                                                             step_len, :]
                third_img_stack = []
                last_ind3 = int((nz3 - step_len) // step)
                for k in range(last_ind3 + 1):
                    wandb.log({'small step': k})
                    print(k)
                    third_lr_vec = second_lr_vec[..., :,
                                                 :, k * step:k * step + step_len]
                    g_output = G_net(third_lr_vec).detach().cpu()
                    g_output = ImageTools.fractions_to_ohe(g_output)
                    g_output_grey = ImageTools.one_hot_decoding(
                        g_output).astype('int8').squeeze()
                    if k == 0:  # keep the beginning
                        g_output_grey = g_output_grey[:, :, :-high_overlap]
                    elif k == last_ind3:  # keep the middle+end
                        g_output_grey = g_output_grey[:, :, high_overlap:]
                    else:  # keep the middle
                        g_output_grey = g_output_grey[:, :, high_overlap:
                                                      - high_overlap]
                    third_img_stack.append(np.int8(g_output_grey))
                res2 = np.concatenate(third_img_stack, axis=2)
                if j == 0:
                    res2 = res2[:, :-high_overlap, :]
                elif j == last_ind2:
                    res2 = res2[:, high_overlap:, :]
                else:
                    res2 = res2[:, high_overlap:-high_overlap, :]
                second_img_stack.append(res2)
            res1 = np.concatenate(second_img_stack, axis=1)
            if i == 0:
                res1 = res1[:-high_overlap, :, :]
            elif i == last_ind1:
                res1 = res1[high_overlap:, :, :]
            else:
                res1 = res1[high_overlap:-high_overlap, :, :]
            first_img_stack.append(res1)
    img = np.concatenate(first_img_stack, axis=0)
    low_res = np.squeeze(ImageTools.one_hot_decoding(im_3d))
    imsave(progress_main_dir + '/' + file_name, img)
    imsave(progress_main_dir + '/' + file_name + 'low_res', low_res)
    # np.save('large_new_vol_nmc', img)