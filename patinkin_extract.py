#!/bin/env python3
import os.path
import sys
from hashlib import md5
import subprocess
import shlex

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

    def scale_from_center(self, w=1.0, h=None):
        (cx, cy) = self.center
        (tl_x, tl_y) = self.center_to_top_left
        (br_x, br_y) = self.center_to_bottom_right

        w_amount = w
        h_amount = h or w

        tl_x *= w_amount
        br_x *= w_amount

        tl_y *= h_amount
        br_y *= h_amount

        x_min = cx + tl_x
        y_min = cy + tl_y
        x_max = cx + br_x
        y_max = cy + br_y

        return Rectangle(x_min, y_min, x_max, y_max)

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
        return '-filter:v "crop={}:{}:{}:{}"'.format(
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

        self.top = top
        self.right = right
        self.bottom = bottom
        self.left = left

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
        return detection.frame / float(self.parent.fps)

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

    def each_detections_op_prop(self, op, prop):
        return op([getattr(d, prop) for d in self.detections])

    @property
    def min_x(self):
        return self.each_detections_op_prop(min, 'left')

    @property
    def min_y(self):
        return self.each_detections_op_prop(min, 'top')

    @property
    def max_x(self):
        return self.each_detections_op_prop(max, 'right')

    @property
    def max_y(self):
        return self.each_detections_op_prop(max, 'bottom')

    @property
    def center_to_top_left(self):
        div = 1.0 / len(self.detections)
        xs, ys = zip(*[d.center_to_top_left for d in self.detections])
        return (
            sum(xs) * div,
            sum(ys) * div
        )

    @property
    def center_to_bottom_right(self):
        div = 1.0 / len(self.detections)
        xs, ys = zip(*[d.center_to_bottom_right for d in self.detections])
        return (
            sum(xs) * div,
            sum(ys) * div
        )

    @property
    def coverage_rectangle(self):
        return Rectangle(
            self.min_x,
            self.min_y,
            self.max_x,
            self.max_y,
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

    def extract(self, group, rect, pad_secs,
            tmpl="ffmpeg -y {seek} -i {in} -frames:v {frames} {crop} {scale} -c:v libvpx-vp9 -lossless 1 {out}.webm"):
        pad_frames = int(pad_secs * self.fps)
        seek = group.as_ffmpeg_seek(pad_secs)
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
        scale = '-vf "scale=w={}:h={}" '.format(int(w * s), int(h * s))

        ffmpeg_command = tmpl.format(**{
            'seek': seek,
            'crop': crop,
            'in': os.path.abspath(self.video_path),
            'out': out_name,
            'frames': int(group.frames + (pad_frames*2)),
            'scale': scale,
            })

        # print(w, h, scale)
        # print(ffmpeg_command)
        subprocess.run(shlex.split(ffmpeg_command))
        # subprocess.run(['ffmpeg', ffmpeg_command])



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
                    pd.extract(group, rect, pad)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('usage: {} <videofile, ...>')

    path_pairs = [(p, os.path.splitext(p)[0] + '.tsv') for p in sys.argv[1:]]
    path_pairs = [(p, t) for (p, t) in path_pairs if os.path.exists(p) and os.path.exists(t)]

    for (vp, tp) in path_pairs:
        pd = PatinkinData(vp, tp)

        max_gaps = [
                0.5,
                2.0,
        ]

        scales = [
                (1.4, 3.0),
                (3.0, 1.4),
                (2.0, 1.8),
                (3.0,),
                (8.0,),
        ]

        pads = [
                0.16,
                1.0,
        ]

        process_variants(pd, max_gaps, scales, pads)
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

