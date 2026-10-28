import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import arrowshot as a  # noqa: E402

DIRS = list(a.DIRS)
# bigarrow's test display: built-in 1512x982, menu bar on top (visible 949 pt high).
SCREEN, VISIBLE, SIGN = (1512, 982), a.R(0, 33, 1512, 949), (421, 84)


def plan(direction, size="M", shape="bend", target=a.P(756, 500), corners="round"):
    return a.plan(target, SIGN, SCREEN, "arrow", size, direction, corners, shape, visible=VISIBLE)


def bbox(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return a.R(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


class GoldenSnapshot(unittest.TestCase):
    """bigarrow's GoldenSnapshotTests: same scene, same tolerance (<=0.5% of pixels off by more than 24)."""

    def test_matches_upstream_golden_png(self):
        g = a.plan(a.P(470, 120), (220, 64), (640, 400), "arrow", "M", "bottom-left")
        S, red = a.SS, a.parse_color("red")
        img = Image.new("RGB", (640 * S, 400 * S), (255, 255, 255))
        d = ImageDraw.Draw(img)
        a.stroke_poly(d, g["shaft"], 15, red)
        a.fill_poly(d, g["head"], red)
        a.fill_poly(d, g["root"], red)
        s = g["sign"]
        d.rectangle([s.minx * S, s.miny * S, s.maxx * S - 1, s.maxy * S - 1], fill=red)
        ours = img.reduce(S).tobytes()
        golden = Image.open(HERE / "golden-arrow.png").convert("RGB").tobytes()
        off = sum(1 for i in range(0, len(ours), 3) if max(abs(ours[i + k] - golden[i + k]) for k in range(3)) > 24)
        self.assertLessEqual(off / (640 * 400), 0.005, f"{off} pixels differ")


class UpstreamGeometryTests(unittest.TestCase):
    """Ports of bigarrow's ArrowGeometryTests / ArrowShapeTests assertions."""

    def test_tip_hits_target_for_every_direction(self):
        for d in DIRS:
            g = plan(d)
            self.assertLessEqual(g["tip"].dist(a.P(756, 500)), 0.5)
            self.assertLessEqual(g["head"][0].dist(a.P(756, 500)), 0.5)

    def test_head_points_along_shaft_end(self):
        for d in DIRS:
            g = plan(d)
            self.assertGreater((g["shaft_end"] - g["control"]).unit.dot(g["direction"]), 0.9999)
            self.assertGreater((g["tip"] - g["head"][2]).unit.dot(g["direction"]), 0.9999)

    def test_everything_stays_on_screen_and_honours_direction(self):
        screen = a.R(0, 0, *SCREEN)
        for d in DIRS:
            g = plan(d)
            self.assertTrue(screen.contains(bbox(g["shaft"]).inset(-7.5, -7.5)), d)
            self.assertTrue(screen.contains(bbox(g["head"])), d)
            self.assertTrue(screen.contains(g["sign"]), d)
            self.assertEqual(g["from"], d)

    def test_rect_target_gets_box_and_tip_below_it(self):
        g = a.plan(a.R(600, 400, 200, 80), SIGN, SCREEN, "arrow", "M", "bottom", visible=VISIBLE)
        self.assertEqual(g["mark"], ("box", a.R(592, 392, 216, 96)))
        self.assertLess(g["tip"].dist(a.P(700, 488 + 6)), 0.5)

    def test_every_shape_hits_target_and_stays_on_screen(self):
        screen = a.R(0, 0, *SCREEN)
        for shape in ("bend", "straight", "zigzag", "spiral"):
            for d in DIRS:
                g = plan(d, shape=shape)
                self.assertLessEqual(g["tip"].dist(a.P(756, 500)), 0.5)
                self.assertGreater((g["shaft_end"] - g["control"]).unit.dot(g["direction"]), 0.9999, (shape, d))
                self.assertTrue(screen.contains(bbox(g["shaft"])), (shape, d))

    def test_straight_shaft_is_a_line(self):
        for d in DIRS:
            g = plan(d, shape="straight")
            line = (g["shaft_end"] - g["tail"]).unit
            self.assertGreater((g["control"] - g["tail"]).unit.dot(line), 0.9999)
            self.assertGreater(g["direction"].dot(line), 0.9999)

    def test_zigzag_leaves_straight_and_runs_into_head(self):
        for d in DIRS:
            g = plan(d, shape="zigzag")
            pts = g["shaft"]
            self.assertGreaterEqual(len(pts), 5)
            self.assertGreater((pts[1] - pts[0]).unit.dot(g["junction"][1]), 0.999)
            self.assertGreater((pts[-1] - pts[-2]).unit.dot(g["direction"]), 0.9999)

    def test_flare_starts_inside_sign_and_heads_out(self):
        for d in DIRS:
            for size in "SML":
                g = plan(d, size=size)
                self.assertTrue(g["sign"].contains(a.R(g["tail"].x, g["tail"].y, 0, 0)))
                s, c, e = g["leg"]
                self.assertGreater(a.quad_tangent(s, c, e, 0).dot(g["junction"][1]), 0)


class ContactSheet(unittest.TestCase):
    def test_layout_matches_scrawl(self):
        # Fixture written by scrawl's own plan_contact_sheet_rows + cell code, compiled from its Rust source.
        for case in json.loads((HERE / "upstream_sheet.json").read_text()):
            cells, size = a.sheet_cells([tuple(s) for s in case["sizes"]])
            self.assertEqual([list(c) for c in cells], case["cells"], case["sizes"])
            self.assertEqual(list(size), case["size"])

    def test_sheet_image_has_planned_size(self):
        shots = [Image.new("RGB", (1280, 800), (200, 0, 0)) for _ in range(4)]
        self.assertEqual(a.contact_sheet(shots).size, (1575, 992))


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(HERE.parent / "arrowshot.py"), *args], capture_output=True, text=True)

    def test_point_and_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            shot = Path(tmp, "s.png")
            a.demo_screen().save(shot)
            r = self.run_cli("point", str(shot), "--rect", "826,580,114,36", "--text", "Click Allow", "-o", str(Path(tmp, "o.png")))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("arrow from", r.stdout)
            out = Image.open(Path(tmp, "o.png"))
            self.assertEqual(out.size, (1280, 800))
            self.assertNotEqual(out.tobytes(), Image.open(shot).tobytes())
            r = self.run_cli("sheet", str(shot), str(Path(tmp, "o.png")), "-o", str(Path(tmp, "sheet.png")))
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_bad_input_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            shot = Path(tmp, "s.png")
            Image.new("RGB", (800, 600)).save(shot)
            self.assertNotEqual(self.run_cli("point", str(shot), "--at", "1,2,3").returncode, 0)
            r = self.run_cli("point", str(shot), "--at", "100,100", "--color", "mauve")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("not a preset", r.stderr)


if __name__ == "__main__":
    unittest.main()
