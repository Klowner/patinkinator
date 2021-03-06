#!/bin/env python3
import cv2
import face_recognition
import glob
import os.path
import sys


def load_patinkin_encodings():
    pwd = os.path.dirname(os.path.realpath(__file__))
    for filename in glob.glob(pwd + '/patinkins/*'):
        image = face_recognition.load_image_file(filename)
        encoding = face_recognition.face_encodings(image)[0]
        yield encoding


def process_video(videopath):
    cap = cv2.VideoCapture(videopath)
    patinkin_encodings = list(load_patinkin_encodings())

    logfile = open(os.path.splitext(videopath)[0] + '.tsv', 'w')
    video_fps = int(cap.get(cv2.CAP_PROP_FPS))
    video_width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    video_height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)

    # log: fps, width, height
    logfile.write('\t'.join(str(int(x)) for x in [
        cap.get(cv2.CAP_PROP_FPS),
        cap.get(cv2.CAP_PROP_FRAME_WIDTH),
        cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
    ]) + '\n')

    skip_frames = 0

    while cap.isOpened():

        matched_location = None

        ret, frame = cap.read()
        if not ret:
            break

        if skip_frames == 0:
            scale_max = 300.0
            scale_by = min(1.0, min(scale_max / video_width, scale_max / video_height))
            small_frame = cv2.resize(frame, (0, 0), fx=scale_by, fy=scale_by)
            face_locations = face_recognition.face_locations(small_frame)
            face_encodings = face_recognition.face_encodings(small_frame, face_locations)

            for index, face_encoding in enumerate(face_encodings):
                matches = face_recognition.compare_faces(patinkin_encodings, face_encoding)
                if True in matches:
                    matched_location = face_locations[index]
                    skip_frames = 1
                else:
                    skip_frames = 2


            if matched_location:
                (top, right, bottom, left) = [int(x * (1.0/scale_by)) for x in matched_location]
                cv2.rectangle(frame, (left, top), (right, bottom), (0, 0, 255), 2)

                cv2_frame_no = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
                # log: frame, top, right, bottom, left
                output = '\t'.join([str(x) for x in [cv2_frame_no, top, right, bottom, left]])
                logfile.write(output + '\n')
                print(output)

            show = False
            if show:
                for fl in face_locations:
                    (top, right, bottom, left) = [int(x * (1.0/scale_by)) for x in fl]
                    cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)

                if matched_location:
                    (top, right, bottom, left) = [int(x * (1.0/scale_by)) for x in matched_location]
                    cv2.rectangle(frame, (left, top), (right, bottom), (0, 0, 255), 2)

                cv2.imshow('frame', cv2.resize(frame, (0, 0), fx=0.25, fy=0.25))
                if cv2.waitKey(1) & 0xff == ord('q'):
                    break

        skip_frames = skip_frames - 1 if skip_frames > 0 else 0
    logfile.close()
    cap.release()
    cv2.destroyAllWindows()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('usage: {} <videofile, ...>'.format(sys.argv[0]))

    existing_paths = [p for p in sys.argv[1:] if os.path.exists(p)]
    for p in existing_paths:
        process_video(p)
