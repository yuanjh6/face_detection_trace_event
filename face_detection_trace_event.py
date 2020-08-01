import copy
import itertools
import logging
import threading
from datetime import datetime
from queue import Queue

import cv2
import face_recognition
import numpy as np

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = logging.FileHandler("log.txt")
handler.setLevel(logging.INFO)
logger = logging.getLogger(__name__)
logger.addHandler(handler)
VIDEO_IMG = 'video_img'


class Track(object):
    __id = 0

    def __init__(self, tracker, frame, box, encoding, history=5):
        self.__id = Track.__id = Track.__id + 1
        self.tracker = tracker
        self.img = frame[box[1]:box[1] + box[3], box[0]:box[0] + box[2]]
        self.frame = frame
        self.encoding = encoding
        self.__history = [False] * history
        self.__history_iter = itertools.cycle(range(history))
        # 暂不用self.__history_have=bool

        self.tracker.init(frame, tuple(box))
        self.__history[next(self.__history_iter)] = True
        Track.new_face_callback(self.__id, self.frame, box)

    def update_img(self, frame, box, encoding):
        self.img = frame[box[1]:box[1] + box[3], box[0]:box[0] + box[2]]
        self.encoding = encoding
        self.frame = frame
        iter_num = next(self.__history_iter)
        if self.alive() and self.__history[iter_num] == 1 and sum(self.__history) == 1:
            Track.lost_face_callback(self.id, self.frame, box)
            self.__history[iter_num] = False

    def update(self, frame):
        return self.tracker.update(frame)

    def alive(self):
        return sum(self.__history) > 0

    @property
    def id(self):
        return self.__id

    @staticmethod
    def new_face_callback(track_id, frame=None, box=None):
        DetectionTrack.draw_boxes(frame, box)
        cv2.imwrite('%s/new_face_%s.png' % (VIDEO_IMG, track_id), frame)
        logger.info('new face datetime:%s track_id: %s img:%s box:%s' % (
            datetime.now().strftime('%Y%m%d%H%M%S'), track_id, '%s/new_face_%s.png' % (VIDEO_IMG, track_id), box))

    @staticmethod
    def lost_face_callback(track_id, frame=None, box=None):
        DetectionTrack.draw_boxes(frame, box)
        cv2.imwrite('%s/lost_face_%s.png' % (VIDEO_IMG, track_id), frame)
        logger.info('lost face datetime:%s track_id: %s img:%s box:%s' % (
            datetime.now().strftime('%Y%m%d%H%M%S'), track_id, '%s/lost_face_%s.png' % (VIDEO_IMG, track_id), box))


class DetectionTrack(object):
    def __init__(self, face_detector, detecton_freq):
        self.__last_frame = None
        self.detecton_freq_iter = itertools.cycle(range(detecton_freq))
        self.is_start = False
        self.is_realtime = False
        self.tracks = list()
        self.cap_thread = None
        self.det_thread = None
        self.face_detector = face_detector
        self.frame_queue = Queue(maxsize=100)
        self.ipc_path = None

    def start(self, ipc_path=None):
        self.ipc_path = ipc_path
        if not self.is_start:
            self.is_start = True
            self.cap_thread = threading.Thread(target=self._start_capture, args=(ipc_path,))
            self.det_thread = threading.Thread(target=self._start_detection_trace)
            self.cap_thread.start()
            self.det_thread.start()

    def _start_capture(self, ipc_path=None):
        cv_cap = cv2.VideoCapture(ipc_path)
        while self.is_start:
            ret, frame = cv_cap.read()
            if ret:
                if self.is_realtime:
                    self.__last_frame = frame
                else:
                    self.frame_queue.put(frame)
            else:
                if self.is_realtime:
                    self.is_start = False  # stop threading:_start_capture,_start_detection_trace
                    break
                    # todo enhance,some tail frame wo't be run by threading _start_detection_trace
                else:
                    self.frame_queue.put(None)
                    break

    def _get_last_frame(self):
        if self.is_realtime:
            ret, self.__last_frame = self.__last_frame, None
        else:
            ret = self.frame_queue.get()
        return ret

    def _start_detection_trace(self):
        videoCapture = cv2.VideoCapture(self.ipc_path)
        fps = videoCapture.get(cv2.CAP_PROP_FPS)
        size = (
            int(videoCapture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(videoCapture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        )
        out_file_path = "%s_output.avi" % self.ipc_path
        videoWriter = cv2.VideoWriter(
            out_file_path,
            cv2.VideoWriter_fourcc('M', 'P', '4', '2'),  # 编码器
            fps,
            size
        )

        while self.is_start:
            last_frame = self._get_last_frame()
            if last_frame is None:
                if self.is_realtime:
                    continue
                else:
                    break
            if next(self.detecton_freq_iter) == 0:
                boxes = self.__face_dec(last_frame)
            else:
                boxes = self.__face_track(last_frame)
            [DetectionTrack.draw_boxes(last_frame, list(box)) for box in boxes]
            videoWriter.write(last_frame)
            cv2.imshow('xx', last_frame)
            cv2.waitKey(1)
        videoWriter.release()

    def stop(self):
        self.is_start = False

    def __face_dec(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        boxes = self.face_detector.detectMultiScale(gray, 1.15, 5)
        self.__face_upgrade_track(frame, boxes)
        return boxes

    def __face_upgrade_track(self, frame, boxes):
        tracks_map = {track.id: track for track in self.tracks}
        track_ids, track_encodings = list(map(lambda x: x.id, self.tracks)), list(
            map(lambda x: x.encoding, self.tracks))
        boxes_imgs_encoding = list()
        if boxes is not None and list(boxes):
            boxes_imgs_encoding = face_recognition.face_encodings(frame, known_face_locations=boxes)
        box_track_ids = [
            np.array(track_ids, int)[face_recognition.compare_faces(track_encodings, unknown_face_encoding)] for
            unknown_face_encoding in boxes_imgs_encoding]

        new_trackers = [
            tracks_map[box_track_id[0]].update_img(frame, boxes_img, boxes_img_encoding)
            if len(box_track_id) > 0
            else Track(cv2.TrackerBoosting_create(), frame, boxes_img, boxes_img_encoding) for
            box_track_id, boxes_img, boxes_img_encoding in
            zip(box_track_ids, boxes, boxes_imgs_encoding)]
        self.tracks = list(filter(lambda x: x.alive(), self.tracks))  # keep alive tracks only
        self.tracks.extend(list(filter(lambda x: x is not None, new_trackers)))

    def __face_track(self, frame):
        boxes = [list(map(int, track.update(frame)[1])) for track in self.tracks]
        return boxes

    @staticmethod
    def draw_boxes(frame, box):
        x, y, w, h = box
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

    # todo 01 use diff change to decation detection ,02,use yield change param
    # ref Human-detection-and-Tracking
    @staticmethod
    def background_subtraction(previous_frame, frame_resized_grayscale, min_area):
        frameDelta = cv2.absdiff(previous_frame, frame_resized_grayscale)
        thresh = cv2.threshold(frameDelta, 25, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)
        im2, cnts, hierarchy = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        temp = 0
        for c in cnts:
            # if the contour is too small, ignore it
            if cv2.contourArea(c) > min_area:
                temp = 1
        return temp


if __name__ == '__main__':
    ipc_path = 'video/1.mp4'  # sys.argv[1]
    faceadd = "model/haarcascade_frontalface_default.xml"
    face_detector = cv2.CascadeClassifier(faceadd)
    # faces = face_detector.detectMultiScale(gray, 1.15, 5)
    detection_track = DetectionTrack(face_detector, detecton_freq=20)
    detection_track.start(ipc_path)