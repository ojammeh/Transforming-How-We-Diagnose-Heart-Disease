import random
import numpy
import glob
import os
import cv2
import utils.settings as settings
from utils.utils_dicom import DicomWrapper
from scipy.ndimage.interpolation import map_coordinates
from scipy.ndimage.filters import gaussian_filter

random.seed(1301)
numpy.random.seed(1301)


def get_pred_patient_dir(patient_id):
    """
    Return the patient directory based on patient id.
    :param patient_id: patient id
    :return: the patient directory 
    """

    prefix = str(patient_id).rjust(4, '0')
    res = settings.PATIENT_PRED_DIR + prefix + "/"
    create_dir_if_not_exists(res)

    return res


def get_pred_patient_img_dir(patient_id):
    """
    Prediction patient dir.
    :param patient_id: patient id
    :return: the path to the patient dir
    """

    res = get_pred_patient_dir(patient_id) + "all_images/"
    create_dir_if_not_exists(res)
    return res


def get_pred_patient_overlay_dir(patient_id):
    """
    Return the path to overlay directory
    :param patient_id: patient id
    :return: the absolute path
    """

    res = get_pred_patient_dir(patient_id) + "predicted_overlays/"
    create_dir_if_not_exists(res)

    return res


def get_pred_patient_transparent_overlay_dir(patient_id):
    """
    Return the path to transparent overlay directory
    :param patient_id: patient id
    :return: the absolute path
    """

    res = get_pred_patient_dir(patient_id) + "predicted_overlays_transparent/"
    create_dir_if_not_exists(res)
    return res


def get_patient_images(patient_id):
    """
    Get Patient images
    :param patient_id: patient id
    :return: patient images
    """

    return get_patient_files(patient_id, "images")


def get_patient_overlays(patient_id):
    """
    Get patient overlay images
    :param patient_id: patiend id
    :return: patiend images overlay
    """

    return get_patient_files(patient_id, "overlays")


def get_patient_transparent_overlays(patient_id):
    """
    Get patient transparent overlay images.
    :param patient_id: patient id
    :return: get patient transparetn overlay images
    """

    return get_patient_files(patient_id, "transparent_overlays")


def get_patient_files(patient_id, file_type, extension=".png"):
    """
    Get patient files.
    :param patient_id: patiend id
    :param file_type: file type
    :param extension: extension of the file
    :return: return all the file
    """

    src_dir = get_pred_patient_dir(patient_id)

    if file_type == "images":
        src_dir = get_pred_patient_img_dir(patient_id)

    if file_type == "overlays":
        src_dir = get_pred_patient_overlay_dir(patient_id)

    if file_type == "transparent_overlays":
        src_dir = get_pred_patient_transparent_overlay_dir(patient_id)

    prefix = str(patient_id).rjust(4, '0')
    file_paths = get_files(src_dir, prefix + "*" + extension)

    return file_paths


def delete_files(target_dir, search_pattern):
    """
    Delete file from target_dir which has search_pattern.
    :param target_dir: the directory path
    :param search_pattern: pattern
    :return: nothing
    """

    files = glob.glob(target_dir + search_pattern)

    for f in files:
        os.remove(f)


def get_files(scan_dir, search_pattern):
    """
    Get all files form a specific directory.
    :param scan_dir: directory path 
    :param search_pattern: pattern
    :return: get all files from a given directory.
    """

    file_paths = glob.glob(scan_dir + search_pattern)

    return file_paths


def enumerate_sax_files(patient_ids=None, filter_slice_type="sax"):
    """
    Enumerate sax files.
    :param patient_ids: the patient ids
    :param filter_slice_type: filter slice type
    :return: return enumerate dicom data
    """

    for root, _, files in os.walk(settings.BASE_DIR + "data"):
        for file_name in files:
            if file_name.endswith(".dcm"):

                parts = root.split('/')
                patient_id = parts[len(parts) - 3]
                slice_type = parts[len(parts) - 1]
                if filter_slice_type not in slice_type:
                    continue

                if patient_ids is not None:
                    if patient_id not in patient_ids:
                        continue

                dicom_data = DicomWrapper(root + "/", file_name)

                yield dicom_data


def compute_mean_image(src_dir, wildcard, img_size):
    """
    Comput mean image
    :param src_dir: direcotry source
    :param wildcard: wildcard
    :param img_size: image size
    :return: return the mean image
    """

    mean_image = numpy.zeros((img_size, img_size), numpy.float32)
    src_files = glob.glob(src_dir + wildcard)
    random.shuffle(src_files)
    img_count = 0

    for src_file in src_files:
        if "_o.png" in src_file:
            continue

        mat = cv2.imread(src_file, cv2.IMREAD_GRAYSCALE)
        mean_image += mat
        img_count += 1

        if img_count > 2000:
            break

    res = mean_image / float(img_count)

    return res


def compute_mean_pixel_values_dir(src_dir, wildcard, channels):
    """
    Comput mean pixel values from a directory.
    :param src_dir: directory source
    :param wildcard: wildcard
    :param channels: channels
    :return: return the specific value
    """

    src_files = glob.glob(src_dir + wildcard)
    random.shuffle(src_files)
    means = []

    for src_file in src_files:
        mat = cv2.imread(src_file, cv2.IMREAD_GRAYSCALE)
        mean = mat.mean()
        means.append(mean)

        if len(means) > 10000:
            break

    res = sum(means) / len(means)

    return res


def replace_color(src_image, from_color, to_color):
    """
    Replace color
    :param src_image: path to a image
    :param from_color: initial color
    :param to_color: new color
    :return: new image
    """

    data = numpy.array(src_image)
    r1, g1, b1 = from_color
    r2, g2, b2 = to_color

    red, green, blue = data[:, :, 0], data[:, :, 1], data[:, :, 2]
    mask = (red == r1) & (green == g1) & (blue == b1)
    data[:, :, :3][mask] = [r2, g2, b2]

    return data


ELASTIC_INDICES = None


def elastic_transform(image, alpha, sigma, random_state=None):
    """
    Elastic transformation
    :param image: the image
    :param alpha: alpha
    :param sigma: sigma
    :param random_state: random state (optional parameter)
    :return: new image
    """

    global ELASTIC_INDICES
    shape = image.shape

    if ELASTIC_INDICES == None:
        if random_state is None:
            random_state = numpy.random.RandomState(1301)

        dx = gaussian_filter((random_state.rand(*shape) * 2 - 1), sigma, mode="constant", cval=0) * alpha
        dy = gaussian_filter((random_state.rand(*shape) * 2 - 1), sigma, mode="constant", cval=0) * alpha
        x, y = numpy.meshgrid(numpy.arange(shape[0]), numpy.arange(shape[1]))
        ELASTIC_INDICES = numpy.reshape(y + dy, (-1, 1)), numpy.reshape(x + dx, (-1, 1))

    return map_coordinates(image, ELASTIC_INDICES, order=1).reshape(shape)


def prepare_cropped_sax_image(sax_image, clahe=True, intermediate_crop=0, rotate=0):
    """
    Crop the patient image if it's necessary.
    :param sax_image: the image
    :param clahe: boolean value
    :param intermediate_crop: crop size 
    :param rotate: the rotation value
    :return: the new image after apply all the operations
    """

    if rotate != 0:
        rot_mat = cv2.getRotationMatrix2D((sax_image.shape[0] / 2, sax_image.shape[0] / 2), rotate, 1)
        sax_image = cv2.warpAffine(sax_image, rot_mat, (sax_image.shape[0], sax_image.shape[1]))

    if intermediate_crop == 0:
        res = sax_image[settings.CROP_INDENT_Y:settings.CROP_INDENT_Y + settings.TARGET_CROP,
              settings.CROP_INDENT_X:settings.CROP_INDENT_X + settings.TARGET_CROP]
    else:
        crop_indent_y = settings.CROP_INDENT_Y - ((intermediate_crop - settings.TARGET_CROP) / 2)
        crop_indent_x = settings.CROP_INDENT_X - ((intermediate_crop - settings.TARGET_CROP) / 2)
        res = sax_image[crop_indent_y:crop_indent_y + intermediate_crop,
              crop_indent_x:crop_indent_x + intermediate_crop]
        res = cv2.resize(res, (settings.TARGET_CROP, settings.TARGET_CROP))

    if clahe:
        clahe = cv2.createCLAHE(tileGridSize=(1, 1))
        res = clahe.apply(res)

    return res


def prepare_overlay_image(src_overlay_path, target_size, antialias=False):
    """
    Prepare overlay image
    :param src_overlay_path: source path
    :param target_size: target size
    :param antialias: anti alias
    :return: new overlay image
    """

    if os.path.exists(src_overlay_path):
        overlay = cv2.imread(src_overlay_path)
        overlay = replace_color(overlay, (255, 255, 255), (0, 0, 0))
        overlay = replace_color(overlay, (0, 255, 255), (255, 255, 255))
        overlay = overlay.swapaxes(0, 2)
        overlay = overlay.swapaxes(1, 2)
        overlay = overlay[0]
        interpolation = cv2.INTER_AREA if antialias else cv2.INTER_NEAREST
        overlay = cv2.resize(overlay, (target_size, target_size), interpolation=interpolation)
    else:
        overlay = numpy.zeros((target_size, target_size), dtype=numpy.uint8)

    return overlay


def create_dir_if_not_exists(target_dir):
    """
    Create a directory if it doesn't exist.
    :param target_dir: directory
    :return: nothing
    """

    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
