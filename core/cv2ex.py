import cv2
import numpy as np
from pathlib import Path
from core.interact import interact as io
from core import imagelib 
import traceback
import os

# Configure OpenCV thread limits to prevent "Can't spawn new thread: res = 11" errors
# This is set here as a fallback in case it wasn't set in main.py
try:
    # Limit OpenCV threads to prevent resource exhaustion
    # Use environment variable if set, otherwise use a safe default
    max_threads = int(os.environ.get('OPENCV_NUM_THREADS', '4'))
    cv2.setNumThreads(max_threads)
except:
    pass  # If setNumThreads fails, continue anyway

def cv2_imread(filename, flags=cv2.IMREAD_UNCHANGED, loader_func=None, verbose=True):
    """
    allows to open non-english characters path
    """
    try:
        if loader_func is not None:
            bytes = bytearray(loader_func(filename))
        else:
            with open(filename, "rb") as stream:
                bytes = bytearray(stream.read())
        numpyarray = np.asarray(bytes, dtype=np.uint8)
        return cv2.imdecode(numpyarray, flags)
    except:
        if verbose:
            io.log_err(f"Exception occured in cv2_imread : {traceback.format_exc()}")
        return None

def cv2_imwrite(filename, img, *args):
    ret, buf = cv2.imencode( Path(filename).suffix, img, *args)
    if ret == True:
        try:
            # Ensure parent directory exists
            Path(filename).parent.mkdir(parents=True, exist_ok=True)
            with open(filename, "wb") as stream:
                stream.write( buf )
            return True
        except Exception as e:
            io.log_err(f"Failed to write image {filename}: {traceback.format_exc()}")
            return False
    return False

def cv2_resize(x, *args, **kwargs):
    h,w,c = x.shape
    x = cv2.resize(x, *args, **kwargs)
    
    x = imagelib.normalize_channels(x, c)
    return x
    