import glob
import astropy.io.fits as fits
import numpy as np
import sys

# pack_frb/371d420013090900_20200914T142947_20200914T142950.fits


outdir = "./diff/"

filename_dict = {}

for filename in glob.glob('*fits'):
    camera = filename.split('_')[0]
    if filename_dict.has_key(camera):
        filename_dict[camera].append(filename)
    else:
        filename_dict[camera] = [filename]


n_stack = 5

ref_dict = {}

for camera in filename_dict.keys():
    print(camera, len(filename_dict[camera]))


    image_concat = [fits.getdata(image) for image in filename_dict[camera][:n_stack]]

    ref_dict[camera] = np.sum(image_concat, axis=0) / n_stack

for camera in filename_dict.keys():
    for filename in filename_dict[camera]:
        ofilename = outdir + filename

        if filename == ofilename:
            print('ooops')
            sys.exit(1)

        hdu = fits.PrimaryHDU(filename)
        hdu[0].data = hdu[0].data - ref_dict[camera]
        hdu.writeto(ofilename, clobber=False)

        break
    # break
