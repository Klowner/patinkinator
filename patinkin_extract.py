#!/bin/env python3
import os.path
import sys
from hashlib import md5
import subprocess
import shlex
import cv2

def midpoint(a, b):
    elems = [a, b]
    elems.sort()
    (a, b) = elems
    return (b - a) * 0.5 + a


class Rectangle:
    def __init__(self, x_min, y_min, x_max, y_max):
        self.x_min = x_min  # left
        self.y_min = y_min  # top
        self.x_max = x_max  # right
        self.y_max = y_max  # bottom

    def __repr__(self):
        return '<Rectangle ({}, {}) ({}, {}) = {}>'.format(
                self.x_min,
                self.y_min,
                self.x_max,
                self.y_max,
                self.size,
                )

    @property
    def center(self):
        return (
            midpoint(self.x_min, self.x_max),
            midpoint(self.y_min, self.y_max)
        )

    @property
    def center_to_bottom_right(self):
        (cx, cy) = self.center
        return (self.x_max - cx, self.y_max - cy)

    @property
    def center_to_top_left(self):
        (cx, cy) = self.center
        return (self.x_min - cx, self.y_min - cy)

    @property
    def tl(self):
        return (self.x_min, self.y_min)

    @property
    def br(self):
        return (self.x_max, self.y_max)

    def scale_from_center(self, w=1.0, h=None):
        (cx, cy) = self.center
        (halfx, halfy) = (self.x_max - cx, self.y_max - cy)
        (sx, sy) = (w, (h or w))
        halfx *= sx
        halfy *= sy
        return Rectangle(cx - halfx, cy - halfy, cx + halfx, cy + halfy)

    def clip_to(self, patinkin_data):
        x, y = patinkin_data.width, patinkin_data.height
        x_min = self.x_min if self.x_min > 0 else 0
        y_min = self.y_min if self.y_min > 0 else 0
        x_max = self.x_max if self.x_max < x else x
        y_max = self.y_max if self.y_max < y else y
        return Rectangle(x_min, y_min, x_max, y_max)

    def round(self):
        elems = [self.x_min, self.y_min, self.x_max, self.y_max]
        return Rectangle(*[round(x) for x in elems])

    @property
    def x(self):
        return self.x_min

    @property
    def y(self):
        return self.y_min

    @property
    def w(self):
        return self.x_max - self.x_min

    @property
    def h(self):
        return self.y_max - self.y_min

    @property
    def as_ffmpeg_crop(self):
        return 'crop={}:{}:{}:{}'.format(
                self.w,
                self.h,
                self.x,
                self.y,
                )

    @property
    def size(self):
        return (self.w, self.h)

class PatinkinDetection:
    def __init__(self, frame, top, right, bottom, left):
        self.frame = frame
        self.rect = Rectangle(left, top, right, bottom)

    @property
    def center(self):
        return self.rect.center

    @property
    def center_to_bottom_right(self):
        return self.rect.center_to_bottom_right

    @property
    def center_to_top_left(self):
        return self.rect.center_to_top_left


class PatinkinDetectionGroup:
    def __init__(self, parent, detections):
        self.parent = parent
        self.detections = detections

    @property
    def length(self):
        return len(self.detections)

    @property
    def seconds(self):
        return len(self.detections) / float(self.parent.fps)

    def time_of(self, detection):
        return (detection.frame) / float(self.parent.fps)

    @property
    def frame_start(self):
        return self.detections[0].frame

    @property
    def frame_end(self):
        return self.detections[-1].frame

    @property
    def frames(self):
        return self.frame_end - self.frame_start

    def as_ffmpeg_seek(self, pad_seconds=0):
        secs = self.time_start_seconds - pad_seconds
        m, s = divmod(secs, 60)
        h, m = divmod(m, 60)
        return "-ss %02d:%02d:%f" % (h, m, s)

    def as_ffmpeg_to(self, pad_seconds=0):
        secs = self.time_end_seconds + pad_seconds
        m, s = divmod(secs, 60)
        h, m = divmod(m, 60)
        return "-to %02d:%02d:%f" % (h, m, s)

    def as_ffmpeg_skip_filter(self):
        return "select='gte(n\, {})'".format(self.frame_start)

    @property
    def time_start_seconds(self):
        return self.time_of(self.detections[0])

    @property
    def time_end_seconds(self):
        return self.time_of(self.detections[-1])

    @property
    def avg_center_pos(self):
        x, y = 0, 0
        div = 1.0 / len(self.detections)
        for d in self.detections:
            (mx, my) = d.center
            x += div * mx
            y += div * my
        return (int(x), int(y))

    def each_detection_rect_get(self, prop):
        return [getattr(d.rect, prop) for d in self.detections]

    @property
    def x_min(self):
        return min(self.each_detection_rect_get('x_min'))

    @property
    def y_min(self):
        return min(self.each_detection_rect_get('y_min'))

    @property
    def x_max(self):
        return max(self.each_detection_rect_get('x_max'))

    @property
    def y_max(self):
        return max(self.each_detection_rect_get('y_max'))

    @property
    def coverage_rectangle(self):
        return Rectangle(
            self.x_min,
            self.y_min,
            self.x_max,
            self.y_max,
        )


class PatinkinData:
    def __init__(self, video_path, tsv_path):
        self.video_path = video_path
        self.tsv_path = tsv_path

        def read_line_ints(line):
            return [int(x.strip()) for x in line.split('\t')]

        with open(tsv_path, 'r') as f:
            (self.fps, self.width, self.height) = read_line_ints(f.readline())
            self.detections = [PatinkinDetection(*read_line_ints(line)) for line in f]

    def grouped(self, max_gap_seconds=0):
        last_frame_no = 0
        max_gap_seconds = max(max_gap_seconds, 1.0 / self.fps) # clamp to 1 frame

        def should_split(frame_no):
            result = ((frame_no - last_frame_no)) / float(self.fps) > max_gap_seconds
            return result

        detection_group = []
        for detection in self.detections:
            if should_split(detection.frame):
                if len(detection_group) > 1:
                    yield PatinkinDetectionGroup(self, detection_group)
                detection_group = []
            detection_group.append(detection)
            last_frame_no = detection.frame

        if detection_group:
            yield PatinkinDetectionGroup(self, detection_group)

    def extract_ffmpeg(self, group, rect, pad_secs,
            tmpl="ffmpeg -y -i {in} {seek} {to} -an -filter:v \"{crop},{scale}\"  {out}.avi"):
        #tmpl="ffmpeg -y -i {in} {seek} {to} -an -filter:v \"{crop},{scale}\" -c:v libvpx-vp9 -lossless 1 {out}.webm"):
        pad_frames = int(pad_secs * self.fps)
        seek = group.as_ffmpeg_seek(pad_secs)
        to = group.as_ffmpeg_to(pad_secs)
        crop = rect.as_ffmpeg_crop

        h = md5()
        h.update(self.video_path.encode('utf-8'))
        h.update(''.join([str(x) for x in rect.size]).encode('utf-8'))
        h.update(''.join([str(x) for x in [group.frame_start, group.frame_end]]).encode('utf-8'))
        out_hash = h.hexdigest()[:8]


        out_name = '_'.join([str(x) for x in [
            "%06x" % (group.frame_start - pad_frames),
            "%04d" % (group.frames + pad_frames*2),
            out_hash,
            ]])

        out_name = './out/{}'.format(out_name)

        # constrain max width/height but keep aspect ratio
        max_dim = 800.0
        w, h = rect.size
        s = min(max_dim / w, max_dim / h)
        scale = 'scale=w={}:h={}'.format(int(w * s), int(h * s))

        ffmpeg_command = tmpl.format(**{
            'seek': seek,
            'to': to,
            'crop': crop,
            'in': os.path.abspath(self.video_path),
            'out': out_name,
            'frames': int(group.frames + (pad_frames*2)),
            'scale': scale,
            })

        # print(w, h, scale)
        print(ffmpeg_command)
        subprocess.run(shlex.split(ffmpeg_command))
        # subprocess.run(['ffmpeg', ffmpeg_command])

    def extract_cv2(self, group, rects):
        cap = cv2.VideoCapture(self.video_path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, group.frame_start)
        cur_frame = group.frame_start
        while cur_frame <= group.frame_end:
            cur_frame += 1
            ret, frame = cap.read()

            if ret:
                colors = [
                        (0,0,255),
                        (0,255,0),
                        (255,0,0),
                        (255,255,0),
                ]

                for i, rect in enumerate(rects):
                    cv2.rectangle(frame, rect.tl, rect.br, colors[i], 2)

                cv2.imshow('window', frame)
                if cv2.waitKey(1) & 0xff == ord('q'):
                    return False

        cap.release()
        return True


    def first_frame(self, group, rect):
        seek = group.as_ffmpeg_seek(0)
        skip = group.as_ffmpeg_skip_filter()
        crop = rect.as_ffmpeg_crop

        h = md5()
        h.update(self.video_path.encode('utf-8'))
        h.update(''.join([str(x) for x in rect.size]).encode('utf-8'))
        h.update(''.join([str(x) for x in [group.frame_start, group.frame_end]]).encode('utf-8'))
        out_hash = h.hexdigest()[:8]
        out_name = '_'.join([str(x) for x in [
            "%06x" % (group.frame_start),
            "%04d" % (group.frames),
            out_hash,
            ]])
        out_name = './out/{}'.format(out_name)

        tmpl = "ffmpeg -y {seek} -i {in} -filter:v \"{crop}\" -vframes 1 -an {out}.jpg"
        ffmpeg_command = tmpl.format(**{
            'seek': seek,
            'in': os.path.abspath(self.video_path),
            'out': out_name,
            'crop': crop,
            'skip': skip,
            })
        print(ffmpeg_command)
        subprocess.run(shlex.split(ffmpeg_command))


def first_frames(data):
    cap = cv2.VideoCapture(data.video_path)

    for group in data.grouped(1):
        rect = group.coverage_rectangle
        cap.set(cv2.CAP_PROP_POS_FRAMES, group.frame_start)
        cur_frame = group.frame_start
        while cur_frame < group.frame_end:
            cur_frame += 1
            ret, frame = cap.read()

            cv2.rectangle(frame, rect.tl, rect.br, (0, 0, 255), 2)
            rect2 = rect.scale_from_center(2.0).clip_to(data).round()
            cv2.rectangle(frame, rect2.tl, rect2.br, (0, 255, 0), 2)
            rect2 = rect.scale_from_center(1.95, 4.0).clip_to(data).round()
            cv2.rectangle(frame, rect2.tl, rect2.br, (255, 0, 0), 2)

            cv2.imshow('window', frame)
            ch = 0xFF & cv2.waitKey(1)
            if ch == ord('q'):
                break
        # print(group.frame_start, group.frames, rect)
        # pd.first_frame(group, rect)


def process_variants(data, max_gaps=[0], scales=[(1.0, 1.0)], pads=[0]):
    for gap in max_gaps:
        for i, group in enumerate(data.grouped(gap)):
            for pad in pads:
                for scale in scales:
                    xs, xy = (scale + scale)[:2]
                    rect = group.coverage_rectangle
                    rect = rect.scale_from_center(xs, xy)
                    rect = rect.clip_to(data)
                    rect = rect.round()
                    pd.extract_ffmpeg(group, rect, pad)


def process_cv2(data, max_gaps=[0], scales=[(1.0, 1.0)], pads=[0]):
    for gap in max_gaps:
        for group in data.grouped(gap):
            # Collect all rectangle variations
            g_rect = group.coverage_rectangle
            rects = []
            for scale in scales:
                xs, ys = (scale + scale)[:2]
                rects.append(
                    g_rect
                        .scale_from_center(xs, ys)
                        .clip_to(data)
                        .round()
                )

            if not data.extract_cv2(group, rects):
                return


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('usage: {} <videofile, ...>')

    path_pairs = [(p, os.path.splitext(p)[0] + '.tsv') for p in sys.argv[1:]]
    path_pairs = [(p, t) for (p, t) in path_pairs if os.path.exists(p) and os.path.exists(t)]

    print(path_pairs)
    for (vp, tp) in path_pairs:
        pd = PatinkinData(vp, tp)

        max_gaps = [
                0,
        ]

        scales = [
                (1.5, 1.3),
                (2.3,),
                (1.95, 3.0),
                (4.0, 5.0),
        ]

        pads = [
                0,
                # 0.5,
                # 3.0,
        ]

        process_cv2(pd, max_gaps, scales, pads)
        #process_variants(pd, max_gaps, scales, pads)
        #first_frames(pd)
        # for grp in pd.grouped(8):
        #     print(
        #             grp.seconds,
        #             grp.time_start_seconds,
        #             grp.time_end_seconds,
        #             '\n',
        #             grp.coverage_rectangle.scale_from_center(0.25).clip_to(pd).round(),
        #             grp.coverage_rectangle.scale_from_center(8.0).clip_to(pd).round(),
        #             grp.coverage_rectangle.scale_from_center(2.0, 0.5).clip_to(pd).round(),
        #             grp.coverage_rectangle.clip_to(pd).round(),
        #             pd.width,
        #             pd.height,
        #             '\n'
        #             )

