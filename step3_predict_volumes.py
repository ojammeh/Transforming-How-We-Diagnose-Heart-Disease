import csv
import math
import ntpath
import os.path
import shutil
import cv2
import numpy
import pandas
import numpy as np

import utils.settings as settings
import utils.utils as utils
from step2_train_segmenter import LVSegmentation

MODEL_NAME = settings.MODEL_NAME
CROP_SIZE = settings.CROP_SIZE
INPUT_SIZE = settings.TARGET_CROP - CROP_SIZE

USE_FRUSTUM_VOLUME_CALCULATIONS = True
USE_EMPTY_FIRST_ITEMIN_FRUSTUM = True

INTERPOLATE_SERIES = False
SMOOTHEN_FRAMES = True

PROCESS_IMAGES = True
SEGMENT_IMAGES = True
COUNT_PIXELS = True
COMPUTE_VOLUMES = True

PREDICTION_FILENAME = "prediction_raw_" + MODEL_NAME + ".csv"
LOW_CONFIDENCE_PIXEL_THRESHOLD = 200

current_debug_line = []
global_dia_errors = []
global_sys_errors = []


def prepare_patient_images(patient_id, intermediate_crop=0):
    """
    Prepare patient images. Create the patient folder. Crop the patient image if it's necessary 
    and save it in a new directory.
    :param patient_id: the patient id.
    :param intermediate_crop: optional parameter
    :return: nothing
    """

    file_lst = []
    prefix = str(patient_id).rjust(4, '0')
    src_files = utils.get_files(settings.BASE_PREPROCESSEDIMAGES_DIR, prefix + "*.png")

    patient_dir = utils.get_pred_patient_dir(patient_id)
    utils.create_dir_if_not_exists(patient_dir)
    patient_img_dir = utils.get_pred_patient_img_dir(patient_id)
    utils.create_dir_if_not_exists(patient_img_dir)
    utils.delete_files(patient_img_dir, "*.png")

    dummy = numpy.zeros((settings.TARGET_SIZE, settings.TARGET_SIZE))
    cv2.imwrite(patient_img_dir + "dummy_overlay.png", dummy)

    for src_path in src_files:
        file_name = ntpath.basename(src_path)
        org_img = cv2.imread(src_path, cv2.IMREAD_GRAYSCALE)
        cropped_img = utils.prepare_cropped_sax_image(org_img, clahe=True, intermediate_crop=intermediate_crop,
                                                      rotate=0)

        cv2.imwrite(patient_img_dir + file_name, cropped_img)
        file_lst.append([file_name, "dummy_overlay.png"])

    with open(patient_img_dir + "pred.lst", "w") as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerows(file_lst)


def predict_overlays_patient(patient_id, save_transparents=False):
    """
    Predict the left ventricle with neural network.
    :param patient_id: patient id
    :param save_transparents: boolean value
    :return: nothing
    """

    segmenter = LVSegmentation()

    src_image_dir = utils.get_pred_patient_img_dir(patient_id)
    overlay_dir = utils.get_pred_patient_overlay_dir(patient_id)
    utils.delete_files(overlay_dir, "*.png")
    transparent_overlay_dir = utils.get_pred_patient_transparent_overlay_dir(patient_id)
    utils.delete_files(transparent_overlay_dir, "*.png")

    num_lines = sum(1 for l in open(src_image_dir + "pred.lst"))
    batch_size = 1

    for try_size in [2, 3, 4, 5]:
        if num_lines % try_size == 0:
            batch_size = try_size

    prefix = str(patient_id).rjust(4, '0')
    src_files = utils.get_files(src_image_dir, prefix + "*.png")

    for i in range(0, num_lines, batch_size):
        src_files_batch = src_files[i:i + batch_size]
        images = []

        for src_file in src_files_batch:
            image = cv2.imread(src_file, cv2.IMREAD_GRAYSCALE)
            images.append(image)

        images = np.array(images)
        images = np.float32(images)

        original_images = images

        images -= np.mean(images, dtype=np.float32)
        images /= np.std(images, dtype=np.float32)

        images = np.reshape(images, (-1, 224, 224, 1))

        predictions = segmenter.predict(images)

        for j in range(len(predictions)):
            file_name = ntpath.basename(src_files_batch[j])
            image = original_images[j]
            prediction = predictions[j]

            image[prediction == 1] = 255
            image[prediction == 0] = 0

            cv2.imwrite(overlay_dir + file_name, image)

            if save_transparents:
                channels = cv2.split(image)
                empty = numpy.zeros(channels[0].shape, dtype=numpy.float32)
                alpha = channels[0].copy()
                alpha[alpha == 255] = 75
                channels = (channels[0], channels[0], empty, alpha)

                transparent_overlay = cv2.merge(channels)
                cv2.imwrite(transparent_overlay_dir + file_name, transparent_overlay)

    segmenter.session.close()


def get_filename(file_path):
    """
    Get the file name
    :param file_path: the path to the file
    :return: return the file name
    """

    file_name = ntpath.basename(file_path)
    parts = file_name.split('_')
    file_name = parts[4].replace(".png", "")

    return file_name


def get_frame_no(file_path):
    """
    Get frame number
    :param file_path: path to the file
    :return: number of the frame
    """

    file_name = ntpath.basename(file_path)
    parts = file_name.split('_')
    frame_no = parts[2]

    return frame_no


def get_location_values(string_value):
    """
    Get location values
    :param string_value: value in string format
    :return: rezultat
    """

    string_value = string_value.replace("[", "")
    string_value = string_value.replace("]", "")
    parts = string_value.split(' ')
    res = [float(x) for x in parts if x]

    return res


def compute_distance(current_value, previous_value):
    """
    Compute the distance between current value and next value.
    :param current_value: current value
    :param previous_value: next value
    :return: distance
    """

    if str(previous_value) == "nan":
        return previous_value

    cur_values = get_location_values(current_value)
    prev_values = get_location_values(previous_value)

    deltas = []

    for c, p in zip(cur_values, prev_values):
        deltas.append((c - p) * (c - p))

    delta_sum = sum(deltas)
    delta_sqrt = math.sqrt(delta_sum)
    return delta_sqrt

    delta = current_value - previous_value
    delta = delta.fillna(0)
    updown = pandas.Series(delta.apply(lambda x: 0 if x == 0 else 1 if x > 0 else -1))

    return updown


def interpolate_series(pixel_series):
    """
    Interpolate series
    :param pixel_series: pixel series
    :return: new pixel series
    """

    if not INTERPOLATE_SERIES:
        return pixel_series

    max_index = 0
    max_value = 0

    for i in range(len(pixel_series)):
        if pixel_series[i] >= max_value:
            max_index = i
            max_value = pixel_series[i]

    max_start = 0
    start_index = 0

    while start_index < max_index:

        if pixel_series[start_index] < max_start:
            next_index = start_index
            next_value = max_value

            while next_index <= max_index:

                if pixel_series[next_index] > max_start:
                    next_value = pixel_series[next_index]
                    break

                next_index += 1

            pixel_series[start_index] = (max_start + next_value) / 2

        max_start = max(max_start, pixel_series[start_index])
        start_index += 1

    max_end = 0
    end_index = len(pixel_series) - 1

    while end_index > max_index:

        if pixel_series[end_index] < max_end:
            next_index = end_index
            next_value = max_value

            while next_index >= max_index:
                if pixel_series[next_index] > max_end:
                    next_value = pixel_series[next_index]
                    break

                next_index -= 1

            pixel_series[end_index] = (max_end + next_value) / 2

        max_end = max(max_end, pixel_series[end_index])
        end_index -= 1

    return pixel_series


def count_pixels(patient_id, all_slice_data, model_name):
    """
    Count the pixels from the left ventricle.
    :param patient_id: patient id
    :param all_slice_data: all slice date from CVS file.
    :param model_name: neural network name.
    :return: data frame
    """

    patient_slice_data = all_slice_data[all_slice_data["patient_id"] == patient_id].copy()
    patient_slice_data["slice_noloc"] = patient_slice_data["slice_no"].map(str) + "_" + patient_slice_data[
        "slice_location"].map(str)
    slices = patient_slice_data["slice_noloc"].unique().tolist()
    frames = patient_slice_data["frame_no"].unique().tolist()

    for name, records in patient_slice_data.groupby("slice_noloc"):
        slice_frames = records["frame_no"].unique()

        if (len(frames) / 2) >= len(slice_frames):
            slices.remove(name)
            continue

        frames = list(set(frames) & set(slice_frames))

    file_name_slices = patient_slice_data.set_index('file_name')['slice_noloc'].to_dict()
    file_name_frames = patient_slice_data.set_index('file_name')['frame_no'].to_dict()
    slice_index = {}

    for slice_no in slices:
        slice_index[str(slice_no).rjust(2, '0')] = len(slice_index)

    frame_pixel_series = {}
    frame_confidence_series = {}

    for frame_no in frames:
        frame_pixel_series[str(frame_no).rjust(2, '0')] = [-1] * len(slices)
        frame_confidence_series[str(frame_no).rjust(2, '0')] = [-1] * len(slices)

    overlay_paths = utils.get_patient_overlays(patient_id)

    for overlay_path in overlay_paths:
        overlay_img = cv2.imread(overlay_path, cv2.IMREAD_GRAYSCALE)

        low_confidence_pixel_count = ((overlay_img < LOW_CONFIDENCE_PIXEL_THRESHOLD) & (overlay_img > 20)).sum()

        pixel_count = overlay_img.sum() / 255

        file_name = get_filename(overlay_path)

        if file_name not in file_name_slices:
            continue

        slice_no = file_name_slices[file_name]
        frame_no = file_name_frames[file_name]

        slice_str = str(slice_no).rjust(2, '0')
        frame_str = str(frame_no).rjust(2, '0')

        if slice_str not in slice_index:
            print("Patient " + str(patient_id) + " : slice " + slice_str + " skipped")
            patient_slice_data = patient_slice_data[patient_slice_data["slice_noloc"] != slice_no]
            continue

        if frame_str not in frame_pixel_series:
            print("Patient " + str(patient_id) + " : frame " + frame_str + " skipped")
            patient_slice_data = patient_slice_data[patient_slice_data["frame_no"] != frame_no]
            continue

        frames_pixel_serie = frame_pixel_series[frame_str]
        frame_confidence_serie = frame_confidence_series[frame_str]
        serie_index = slice_index[slice_str]
        frames_pixel_serie[serie_index] = pixel_count
        frame_confidence_serie[serie_index] = low_confidence_pixel_count

    data_frame = pandas.DataFrame()
    patient_slice_data_frame1 = patient_slice_data[patient_slice_data["frame_no"] == 1]
    data_frame["slice"] = slices
    data_frame["slice_thickness"] = patient_slice_data_frame1["slice_thickness"].values
    data_frame["slice_location"] = patient_slice_data_frame1["slice_location"].values
    data_frame["slice_dist"] = (
        patient_slice_data_frame1["slice_location"].shift(-1) - patient_slice_data_frame1["slice_location"]).values
    data_frame["slice_dist"].fillna(data_frame["slice_dist"].mean(), inplace=True)
    data_frame["slice_location2a"] = patient_slice_data_frame1["slice_location2"].values
    data_frame["slice_location2b"] = patient_slice_data_frame1["slice_location2"].shift(-1).values
    data_frame["slice_dist2"] = data_frame.apply(
        lambda row: compute_distance(row["slice_location2a"], row["slice_location2b"]), axis=1)
    data_frame["slice_dist2"].fillna(data_frame["slice_dist2"].mean(), inplace=True)

    deltas = abs(abs(data_frame["slice_dist"]) - abs(data_frame["slice_dist2"])).sum()

    data_frame["time"] = patient_slice_data_frame1["time"].values

    for frame_no in frames:
        frame_str = str(frame_no).rjust(2, '0')
        interpolated_series = interpolate_series(frame_pixel_series[frame_str])
        data_frame["fr_" + frame_str] = interpolated_series

    if SMOOTHEN_FRAMES:
        for frame_no in frames:
            prev_frame = frame_no - 1

            if prev_frame < 1:
                prev_frame = 30

            next_frame = frame_no + 1

            if next_frame > 30:
                next_frame = 1

            this_frame_col = "fr_" + str(frame_no).rjust(2, '0')
            next_frame_col = "fr_" + str(next_frame).rjust(2, '0')
            prev_frame_col = "fr_" + str(prev_frame).rjust(2, '0')

            if this_frame_col in data_frame.columns and next_frame_col in data_frame.columns and prev_frame_col in data_frame.columns:
                data_frame[this_frame_col] = (data_frame[this_frame_col] + data_frame[prev_frame_col] + data_frame[
                    next_frame_col]) / 3

    for frame_no in frames:
        frame_str = str(frame_no).rjust(2, '0')
        data_frame["co_" + frame_str] = frame_confidence_series[frame_str]

    patient_dir = utils.get_pred_patient_dir(patient_id)
    data_frame.to_csv(patient_dir + "/areas_" + model_name + ".csv", sep=";")

    return data_frame


def compute_volumne_frustum(pixel_series, distance_series, low_confidence_calc=False):
    """
    Compute volume
    :param pixel_series: pixels
    :param distance_series: distances
    :param low_confidence_calc: optional value
    :return: result and max value
    """

    val_list = pixel_series.values.tolist()
    dist_list = distance_series.fillna(10).values.tolist()
    max_val = 0

    val_list.append(0)

    if abs(dist_list[0]) > 25:
        dist_list[0] = 0

    if USE_EMPTY_FIRST_ITEMIN_FRUSTUM:
        dist_list.insert(0, max(dist_list[0], 10))
        val_list.insert(0, 0)

    total_volume = 0

    for i in range(len(val_list) - 1):
        val = val_list[i]
        dist = abs(float(dist_list[i]))

        if val > max_val:
            max_val = val

        next_val = val_list[i + 1]

        if (not USE_FRUSTUM_VOLUME_CALCULATIONS) or low_confidence_calc:
            next_val = val

        volume = (dist / 3.)
        term2 = (val + math.sqrt(val * next_val) + next_val)
        volume *= term2
        total_volume += volume

    res = total_volume / 1000

    return res, max_val


def compute_volumes(patient_id, model_name, debug_info=False):
    """
    Calculate the volume of the patient.
    :param patient_id: patient id
    :param model_name: neural network name
    :param debug_info: optional value
    :return: diastole volume, systole volume, low confidence diastole volume,
    low confidence systole volume, diastole frame, systole frame, diastole max slice value, sys max slice value
    """

    patient_dir = utils.get_pred_patient_dir(patient_id)
    min_areas = pandas.read_csv(patient_dir + "/areas_" + model_name + ".csv", sep=";")
    columns = list(min_areas)
    diastole_pixels = 0
    systole_pixels = 999999

    for column in columns:
        if not column.startswith("fr"):
            continue

        value_list = min_areas[column].values.tolist()
        value_list.sort(reverse=True)
        pixel_sum = sum(value_list[:200])

        if pixel_sum > diastole_pixels:
            diastole_pixels = pixel_sum
            diastole_col = column

        if pixel_sum < systole_pixels:
            systole_pixels = pixel_sum
            systole_col = column

    if debug_info:
        current_debug_line.append(str(diastole_col))
        current_debug_line.append(str(systole_col))

    dist_col = "slice_dist"
    min_areas_selection = min_areas[
        ["slice", "slice_thickness", "slice_location", "time", dist_col]].copy()
    min_areas_selection["diastole"] = min_areas[diastole_col].values
    min_areas_selection["diastole_vol"] = (min_areas_selection["diastole"] * min_areas_selection[dist_col]).values
    min_areas_selection["diastole_conf"] = min_areas[diastole_col.replace("fr", "co")].values
    min_areas_selection["systole"] = min_areas[systole_col].values
    min_areas_selection["systole_vol"] = (min_areas_selection["systole"] * min_areas_selection[dist_col]).values
    min_areas_selection["systole_conf"] = min_areas[systole_col.replace("fr", "co")].values
    min_areas_selection.to_csv(patient_dir + "/areas_dia_sys_" + model_name + ".csv", sep=";")

    dia_frame = diastole_col.replace("fr_", "")
    sys_frame = systole_col.replace("fr_", "")
    diastole_vol, dia_max_slice_val = compute_volumne_frustum(min_areas_selection["diastole"],
                                                              min_areas_selection[dist_col])
    systole_vol, sys_max_slice_val = compute_volumne_frustum(min_areas_selection["systole"],
                                                             min_areas_selection[dist_col])

    low_conf_diastole_vol = round(
        compute_volumne_frustum(min_areas_selection["diastole_conf"], min_areas_selection[dist_col])[0], 2)
    low_conf_systole_vol = round(
        compute_volumne_frustum(min_areas_selection["systole_conf"], min_areas_selection[dist_col])[0], 2)

    return diastole_vol, systole_vol, low_conf_diastole_vol, low_conf_systole_vol, dia_frame, sys_frame, dia_max_slice_val, sys_max_slice_val


def evaluate_volume(patient_id, diastole_vol, systole_vol, pred_model_name, scale, lowconf_dia_vol, lowconf_sys_vol,
                    dia_frame, sys_frame, dia_max_slice, sys_max_slice, debug_info=False):
    """
    Evaluate the volume
    :param patient_id: patient id
    :param diastole_vol: diastole volume
    :param systole_vol: systole volum
    :param pred_model_name: neural network model name
    :param scale: scale
    :param lowconf_dia_vol: low confidence diastole module
    :param lowconf_sys_vol: low confidence systole module
    :param dia_frame: diastole frame
    :param sys_frame: systole frame
    :param dia_max_slice: diastole max slice
    :param sys_max_slice: systole max slice
    :param debug_info: optional parameter
    :return: error systole, error diastole
    """

    diastole_vol = round(diastole_vol, 1)
    systole_vol = round(systole_vol, 1)
    pred_data = pandas.read_csv(settings.RESULT_DIR + PREDICTION_FILENAME, sep=";")
    scale_col = "scale"

    if scale_col not in pred_data.columns:
        pred_data[scale_col] = 1

    if "lowconf_dia" not in pred_data.columns:
        pred_data["lowconf_dia"] = 0
        pred_data["lowconf_sys"] = 0

    if "frame_dia" not in pred_data.columns:
        pred_data["frame_dia"] = -1
        pred_data["frame_sys"] = -1

    if "max_dia_slice" not in pred_data.columns:
        pred_data["max_dia_slice"] = 0
        pred_data["max_sys_slice"] = 0

    if SEGMENT_IMAGES:
        pred_data.loc[pred_data["patient_id"] == patient_id, scale_col] = scale
    else:
        scale = pred_data.loc[pred_data["patient_id"] == patient_id, scale_col]
        diastole_vol *= scale
        systole_vol *= scale

    pred_data.loc[pred_data["patient_id"] == patient_id, "pred_dia"] = diastole_vol
    pred_data.loc[pred_data["patient_id"] == patient_id, "pred_sys"] = systole_vol

    pred_data.loc[pred_data["patient_id"] == patient_id, "lowconf_dia"] = lowconf_dia_vol
    pred_data.loc[pred_data["patient_id"] == patient_id, "lowconf_sys"] = lowconf_sys_vol
    pred_data.loc[pred_data["patient_id"] == patient_id, "frame_dia"] = dia_frame
    pred_data.loc[pred_data["patient_id"] == patient_id, "frame_sys"] = sys_frame
    pred_data.loc[pred_data["patient_id"] == patient_id, "max_dia_slice"] = dia_max_slice
    pred_data.loc[pred_data["patient_id"] == patient_id, "max_sys_slice"] = sys_max_slice

    pred_data["error_dia"] = pred_data["pred_dia"] - pred_data["Diastole"]
    pred_data["abserr_dia"] = abs(pred_data["pred_dia"] - pred_data["Diastole"])
    pred_data["error_sys"] = pred_data["pred_sys"] - pred_data["Systole"]
    pred_data["abserr_sys"] = abs(pred_data["pred_sys"] - pred_data["Systole"])

    err_dia = pred_data.loc[pred_data["patient_id"] == patient_id, "error_dia"].values[0]
    err_sys = pred_data.loc[pred_data["patient_id"] == patient_id, "error_sys"].values[0]

    if debug_info:
        current_debug_line.append(str(err_dia))
        current_debug_line.append(str(err_sys))

    pred_data.to_csv(settings.RESULT_DIR + "prediction_raw_" + MODEL_NAME + ".csv", sep=";", index=False)

    return err_dia, err_sys


def predict_patient(patient_id, all_slice_data, pred_model_name, debug_info=False):
    """
    The main method.
    :param patient_id: patient id
    :param all_slice_data: all slices data
    :param pred_model_name: neural network model name
    :param debug_info: optional parameter for debug information
    :return: nothing
    """

    if not os.path.exists(settings.RESULT_DIR + PREDICTION_FILENAME):
        shutil.copyfile(settings.RESULT_DIR + "train_enriched.csv", settings.RESULT_DIR + PREDICTION_FILENAME)

    global current_debug_line
    current_debug_line = [str(patient_id)]

    done = False
    intermediate_crop = 0
    round_no = 0

    while not done:
        if PROCESS_IMAGES:
            prepare_patient_images(patient_id, intermediate_crop=intermediate_crop)

        print("Inceput - Pas 2 - Segmentare")
        print("   > Segmentarea imaginilor")

        if SEGMENT_IMAGES:
            predict_overlays_patient(patient_id, save_transparents=True)

        print("Terminat - Pas 2 - Segmentare")

        print("Inceput - Pas 3 - Prezicerea volumului")

        print("   > Numararea pixelilor")

        if COUNT_PIXELS:
            pixel_frame = count_pixels(patient_id, all_slice_data, pred_model_name)

        print("   > Calcularea volumului")

        if COMPUTE_VOLUMES:
            diastole_vol, systole_vol, diastole_lowconf_vol, systole_lowconf_vol, diastole_frame, systole_frame, diastole_max, systole_max = compute_volumes(
                patient_id, pred_model_name, debug_info=debug_info)

            scale = 1
            if intermediate_crop != 0:
                scale = float(intermediate_crop) / float(settings.TARGET_CROP)
                scale *= scale
                diastole_vol *= scale
                systole_vol *= scale

        print("Terminat - Pas 3 - Prezicerea volumului")

        if debug_info:
            current_debug_line.append(str(round(diastole_vol, 2)))
            current_debug_line.append(str(round(systole_vol, 2)))

        err_dia, err_sys = evaluate_volume(patient_id, diastole_vol, systole_vol, pred_model_name, scale,
                                           diastole_lowconf_vol, systole_lowconf_vol, diastole_frame, systole_frame,
                                           diastole_max, systole_max, debug_info=debug_info)

        if diastole_vol > 340 and round_no == 0 and SEGMENT_IMAGES:
            intermediate_crop = 220
            current_debug_line = [str(patient_id)]
            round_no = 1
        else:
            done = True

        global_dia_errors.append(abs(err_dia))
        global_sys_errors.append(abs(err_sys))

    return None


if __name__ == "__main__":
    slice_data = pandas.read_csv(settings.RESULT_DIR + "dicom_data_enriched.csv", sep=";")
    current_debug_line = ["patient", "dia_col", "sys_col", "dia_vol", "sys_vol", "dia_err", "sys_err"]

    model_name = MODEL_NAME + "_folder"
    range_start = 1
    range_end = 1141

    print("\t".join(map(lambda x: str(x).rjust(10), current_debug_line)))

    print("Predicting model " + model_name)

    for i in range(range_start, range_end):

        predict_patient(i, slice_data, model_name, debug_info=True)

        if len(global_dia_errors) % 20 == 0:
            current_debug_line = ["avg", "", "", "", "", round(sum(global_dia_errors) / len(global_dia_errors), 2),
                                  round(sum(global_sys_errors) / len(global_sys_errors), 2)]
            print("\t".join(map(lambda x: str(x).rjust(10), current_debug_line)))

    global_dia_errors = []
    global_sys_errors = []
