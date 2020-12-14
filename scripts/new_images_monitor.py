import glob



def find_diff_files(old_filelist, glob_string="./*"):
    new_filelist = glob.glob(glob_string)

    diff = set(old_filelist) - set(new_filelist)
    print(diff)


if __name__ == '__main__':
    old = find_diff_files([], "./*")
    new = find_diff_files([], "./*")
