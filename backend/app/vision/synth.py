"""Procedural porch-scene renderer used for SYNTHETIC test fixtures.

Everything produced here is labelled SYNTHETIC_RENDER. It exists so that the
vision pipeline can be evaluated against *exact* ground truth (the true box
footprint is known in world centimetres). It is never presented as Ring footage.

World frame: X to the right (cm), Y away from the door (cm), Z up (cm).
The camera sits at the door (doorbell height) looking out over the walkway.
"""
from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field

import cv2
import numpy as np

W, H = 960, 720


@dataclass
class Camera:
    x: float = 150.0
    y: float = 0.0
    z: float = 130.0
    pitch_deg: float = 40.0
    yaw_deg: float = 0.0
    vfov_deg: float = 55.0
    width: int = W
    height: int = H

    @property
    def f(self) -> float:
        return (self.height / 2) / math.tan(math.radians(self.vfov_deg / 2))

    def K(self) -> np.ndarray:
        return np.array([[self.f, 0, self.width / 2], [0, self.f, self.height / 2], [0, 0, 1.0]])

    def R(self) -> np.ndarray:
        p, yw = math.radians(self.pitch_deg), math.radians(self.yaw_deg)
        fwd = np.array([math.sin(yw) * math.cos(p), math.cos(yw) * math.cos(p), -math.sin(p)])
        right = np.array([math.cos(yw), -math.sin(yw), 0.0])
        down = np.cross(fwd, right)
        return np.stack([right, down, fwd])

    def C(self) -> np.ndarray:
        return np.array([self.x, self.y, self.z])

    def project(self, pts: np.ndarray) -> np.ndarray:
        pts = np.atleast_2d(pts).astype(float)
        pc = (self.R() @ (pts - self.C()).T).T
        uv = (self.K() @ pc.T).T
        return uv[:, :2] / uv[:, 2:3]

    def depth(self, pts: np.ndarray) -> np.ndarray:
        pts = np.atleast_2d(pts).astype(float)
        return (self.R() @ (pts - self.C()).T).T[:, 2]

    def ground_homography(self) -> np.ndarray:
        """3x3 H mapping world ground (X,Y,1) -> image (u,v,1)."""
        K, R, C = self.K(), self.R(), self.C()
        t = -R @ C
        return K @ np.column_stack([R[:, 0], R[:, 1], t])


# ---- layout (world cm) -------------------------------------------------------
WALKWAY_X = (100.0, 210.0)
LANDING = (20.0, 280.0, 0.0, 160.0)  # x0, x1, y0, y1 tiled landing
DOORMAT = (112.0, 198.0, 58.0, 100.0)
DROP_ZONE_WORLD = [(222, 168), (280, 168), (280, 238), (222, 238)]  # parcel area on the gravel beside the walkway
TILE = 40.0


_NOISE: dict = {}


def _world_noise(X: np.ndarray, Y: np.ndarray, key: str, sigma: float) -> np.ndarray:
    """Noise anchored to the floor plane (1 cm grid), so texture moves with the
    world when the camera moves -- like real concrete, grass and tile."""
    if key not in _NOISE:
        rng = np.random.default_rng(zlib.crc32(key.encode()))  # stable across processes (hash() is salted)
        n = rng.normal(0, 1, (1400, 900)).astype(np.float32)  # y in [-200,1200), x in [-300,600)
        n = cv2.GaussianBlur(n, (0, 0), sigma)
        n /= n.std() + 1e-6
        _NOISE[key] = n
    n = _NOISE[key]
    mx = np.clip(X + 300.0, 0, 899).astype(np.float32)
    my = np.clip(Y + 200.0, 0, 1399).astype(np.float32)
    return cv2.remap(n, mx, my, cv2.INTER_LINEAR)


def _floor_color(X: np.ndarray, Y: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    h, w = X.shape
    img = np.zeros((h, w, 3), np.float32)
    # lawn
    noise = _world_noise(X, Y, "lawn", 0.7)
    lawn = np.stack([40 + 10 * noise, 105 + 22 * noise, 60 + 12 * noise], -1)  # BGR
    img[:] = lawn
    # walkway concrete
    walk = (X >= WALKWAY_X[0]) & (X <= WALKWAY_X[1]) & (Y > 0)
    conc_n = _world_noise(X, Y, "concrete", 0.6)
    conc = np.stack([168 + 7 * conc_n, 170 + 7 * conc_n, 172 + 7 * conc_n], -1)
    img[walk] = conc[walk]
    # walkway expansion joints every 90 cm
    joint = walk & (np.abs(((Y + 45) % 90) - 45) < 1.2)
    img[joint] = (120, 122, 124)
    # walkway border edging
    edge = (Y > 0) & ((np.abs(X - WALKWAY_X[0]) < 3) | (np.abs(X - WALKWAY_X[1]) < 3))
    img[edge] = (80, 84, 90)
    # tiled landing
    x0, x1, y0, y1 = LANDING
    land = (X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1)
    tile_n = _world_noise(X, Y, "tile", 0.6)
    tiles = np.stack([120 + 6 * tile_n, 140 + 6 * tile_n, 175 + 6 * tile_n], -1)  # terracotta-ish
    img[land] = tiles[land]
    grout = land & ((np.abs(((X - x0) % TILE) - TILE / 2) > TILE / 2 - 1.0) | (np.abs(((Y - y0) % TILE) - TILE / 2) > TILE / 2 - 1.0))
    img[grout] = (150, 158, 165)
    # doormat with stripes
    mx0, mx1, my0, my1 = DOORMAT
    mat = (X >= mx0) & (X <= mx1) & (Y >= my0) & (Y <= my1)
    stripes = mat & (((X - mx0) // 6) % 2 == 0)
    img[mat] = (40, 52, 70)
    img[stripes] = (30, 40, 55)
    # flower bed left
    bed = (X >= 0) & (X <= 70) & (Y >= 220) & (Y <= 420)
    bed_n = _world_noise(X, Y, "bed", 2.5)
    img[bed] = np.stack([35 + 8 * bed_n, 50 + 10 * bed_n, 75 + 10 * bed_n], -1)[bed]
    # flowers (stationary texture landmarks)
    fl = bed & (bed_n > 1.3)
    img[fl] = (60, 60, 200)
    # gravel parcel area beside the walkway
    gx0, gx1, gy0, gy1 = 222, 280, 168, 238
    grav = (X >= gx0) & (X <= gx1) & (Y >= gy0) & (Y <= gy1)
    gn = _world_noise(X, Y, "gravel", 0.5)
    img[grav] = np.stack([118 + 14 * gn, 126 + 14 * gn, 132 + 14 * gn], -1)[grav]
    # stepping stones on the lawn (stationary landmarks)
    for cx, cy in [(255, 260), (262, 340), (250, 430), (35, 500), (290, 520)]:
        st = (X - cx) ** 2 + ((Y - cy) * 1.3) ** 2 < 14 ** 2
        img[st] = (150, 150, 145)
    return img


@dataclass
class Box:
    x: float  # centre X cm
    y: float  # centre Y cm
    w: float = 45.0  # along X
    d: float = 35.0  # along Y
    h: float = 30.0
    rot_deg: float = 0.0
    color: tuple = (95, 145, 190)  # cardboard (BGR)

    def base_corners(self) -> np.ndarray:
        r = math.radians(self.rot_deg)
        c, s = math.cos(r), math.sin(r)
        pts = []
        for dx, dy in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
            lx, ly = dx * self.w / 2, dy * self.d / 2
            pts.append((self.x + c * lx - s * ly, self.y + s * lx + c * ly))
        return np.array(pts)


@dataclass
class SceneSpec:
    boxes: list = field(default_factory=list)
    camera: Camera = field(default_factory=Camera)
    gain: float = 1.0
    noise: float = 3.0
    blur_sigma: float = 0.0
    obscure: float = 0.0  # fraction 0..1 of lens covered
    seed: int = 7
    jpeg_quality: int = 90
    shadow: bool = True


def render(spec: SceneSpec) -> np.ndarray:
    cam = spec.camera
    rng = np.random.default_rng(1234)  # fixed texture so the reference matches
    us, vs = np.meshgrid(np.arange(cam.width) + 0.5, np.arange(cam.height) + 0.5)
    K_inv = np.linalg.inv(cam.K())
    rays = np.stack([us, vs, np.ones_like(us)], -1) @ K_inv.T
    world_dir = rays @ cam.R()  # R^T applied row-wise
    C = cam.C()
    with np.errstate(divide="ignore", invalid="ignore"):
        t = -C[2] / world_dir[..., 2]
    X = C[0] + t * world_dir[..., 0]
    Y = C[1] + t * world_dir[..., 1]
    sky = (t <= 0) | ~np.isfinite(t)
    X[sky] = 0
    Y[sky] = 0
    img = _floor_color(X, Y, rng)
    img[sky] = (210, 190, 170)
    # mild distance haze
    dist = np.clip(t / 900.0, 0, 1)[..., None]
    img = img * (1 - 0.15 * dist) + 200 * 0.15 * dist

    # sort boxes far -> near (painter)
    boxes = sorted(spec.boxes, key=lambda b: -cam.depth(np.array([[b.x, b.y, 0]]))[0])
    light = np.array([-0.35, 0.55])  # shadow offset direction per cm height
    for b in boxes:
        base = b.base_corners()
        if spec.shadow:
            sh = np.vstack([base, base + light * b.h])
            hull = cv2.convexHull(cam.project(np.column_stack([sh, np.zeros(len(sh))])).astype(np.float32))
            mask = np.zeros(img.shape[:2], np.uint8)
            cv2.fillPoly(mask, [hull.astype(np.int32)], 255)
            mask = cv2.GaussianBlur(mask, (0, 0), 3) / 255.0
            img = img * (1 - 0.38 * mask[..., None])
        bottom = np.column_stack([base, np.zeros(4)])
        top = np.column_stack([base, np.full(4, b.h)])
        faces = []
        for i in range(4):
            j = (i + 1) % 4
            quad = np.array([bottom[i], bottom[j], top[j], top[i]])
            faces.append(("side", i, quad))
        faces.append(("top", -1, top))
        # draw faces visible from camera, far to near
        def face_depth(f):
            return cam.depth(f[2]).mean()

        for kind, idx, quad in sorted(faces, key=lambda f: -face_depth(f)):
            pts = cam.project(quad)
            centre = quad.mean(0)
            if kind == "top":
                shade = 1.12
            else:
                i, j = idx, (idx + 1) % 4
                edge = quad[1] - quad[0]
                normal = np.array([edge[1], -edge[0], 0.0])
                normal /= np.linalg.norm(normal) + 1e-9
                to_cam = cam.C() - centre
                if np.dot(normal, to_cam) < 0:
                    normal = -normal
                shade = 0.72 + 0.25 * max(0.0, float(np.dot(normal, np.array([-0.4, -0.6, 0.0]))))
            col = np.clip(np.array(b.color) * shade, 0, 255)
            cv2.fillPoly(img, [np.round(pts).astype(np.int32)], tuple(float(c) for c in col), lineType=cv2.LINE_AA)
        # packing tape stripe on top
        c, s = math.cos(math.radians(b.rot_deg)), math.sin(math.radians(b.rot_deg))
        tape = []
        for dx, dy in [(-0.12, -1), (0.12, -1), (0.12, 1), (-0.12, 1)]:
            lx, ly = dx * b.w / 2 * 1.0, dy * b.d / 2
            tape.append((b.x + c * lx - s * ly, b.y + s * lx + c * ly, b.h + 0.1))
        cv2.fillPoly(img, [np.round(cam.project(np.array(tape))).astype(np.int32)], (150, 190, 215), lineType=cv2.LINE_AA)

    img = img * spec.gain
    if spec.obscure > 0:
        # an object/hand/leaf pressed against the lens: dark, blurred, covering part of view
        m = np.zeros(img.shape[:2], np.float32)
        r = int(math.sqrt(spec.obscure) * cam.width * 0.75)
        cv2.circle(m, (cam.width // 3, cam.height // 2), r, 1.0, -1)
        m = cv2.GaussianBlur(m, (0, 0), 40)[..., None]
        img = img * (1 - m) + np.array([25, 28, 30]) * m
    if spec.blur_sigma > 0:
        img = cv2.GaussianBlur(img, (0, 0), spec.blur_sigma)
    nrng = np.random.default_rng(spec.seed)
    img = img + nrng.normal(0, spec.noise, img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)
    # honest provenance stamp (NOT a Ring watermark)
    cv2.rectangle(img, (cam.width - 262, 8), (cam.width - 8, 34), (20, 20, 20), -1)
    cv2.putText(img, "SYNTHETIC TEST RENDER", (cam.width - 254, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1, cv2.LINE_AA)
    if spec.jpeg_quality:
        ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, spec.jpeg_quality])
        img = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    return img


def world_poly_to_image(cam: Camera, poly) -> np.ndarray:
    p = np.asarray(poly, float)
    return cam.project(np.column_stack([p, np.zeros(len(p))]))


def normalized(cam: Camera, pts_img: np.ndarray) -> list:
    return [[float(u / cam.width), float(v / cam.height)] for u, v in pts_img]
