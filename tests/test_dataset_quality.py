import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from dataset_basic import validate_dataset  # noqa: E402
from dataset_quality import analyze_dataset_quality  # noqa: E402


def policy() -> dict:
    manifest = yaml.safe_load((ROOT / "gitops/policies/dataset-quality-policy.yaml").read_text())
    return yaml.safe_load(manifest["data"]["policy.yaml"])


class DatasetFixture:
    def __init__(self, root: Path):
        self.root = root
        (root / "data.yaml").write_text(yaml.safe_dump({"nc": 2, "names": ["helmet", "person"]}))
        for split in ("train", "valid", "test"):
            (root / split / "images").mkdir(parents=True)
            (root / split / "labels").mkdir(parents=True)
        self.add("train", "train-a", (255, 0, 0), "0 0.5 0.5 0.2 0.2\n")
        self.add("valid", "valid-a", (0, 255, 0), "1 0.5 0.5 0.2 0.2\n")
        self.add("test", "test-a", (0, 0, 255), "0 0.5 0.5 0.2 0.2\n")

    def add(self, split: str, stem: str, color: tuple[int, int, int], label: str, size=(640, 480)) -> Path:
        image_path = self.root / split / "images" / f"{stem}.jpg"
        Image.new("RGB", size, color).save(image_path)
        (self.root / split / "labels" / f"{stem}.txt").write_text(label)
        return image_path

    def report(self, previous=None) -> dict:
        validate_dataset(self.root)
        return analyze_dataset_quality(self.root, 31, policy(), previous)


class DatasetQualityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = DatasetFixture(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def codes(self, report):
        return {issue["code"] for issue in report["issues"]}

    def test_01_normal_dataset_passes(self):
        report = self.fixture.report()
        self.assertEqual(report["summary"]["status"], "PASSED")
        self.assertEqual(report["dataset"]["image_count"], 3)

    def test_02_broken_image_fails_basic_validation(self):
        (self.fixture.root / "train/images/train-a.jpg").write_bytes(b"not-an-image")
        with self.assertRaises(Exception):
            validate_dataset(self.fixture.root)

    def test_03_invalid_class_id_fails_basic_validation(self):
        (self.fixture.root / "train/labels/train-a.txt").write_text("9 0.5 0.5 0.2 0.2\n")
        with self.assertRaises(RuntimeError):
            validate_dataset(self.fixture.root)

    def test_04_invalid_bbox_fails_basic_validation(self):
        (self.fixture.root / "train/labels/train-a.txt").write_text("0 1.2 0.5 0.2 0.2\n")
        with self.assertRaises(RuntimeError):
            validate_dataset(self.fixture.root)

    def test_05_excessive_empty_labels_are_error(self):
        for index in range(8):
            self.fixture.add("train", f"empty-{index}", (index, index, index), "")
        report = self.fixture.report()
        self.assertEqual(report["summary"]["status"], "ERROR")
        self.assertIn("EMPTY_LABEL_RATIO_ERROR", self.codes(report))

    def test_06_severe_class_imbalance_is_warning(self):
        labels = "".join("0 0.5 0.5 0.2 0.2\n" for _ in range(100))
        (self.fixture.root / "train/labels/train-a.txt").write_text(labels)
        report = self.fixture.report()
        self.assertEqual(report["summary"]["status"], "WARNING")
        self.assertTrue({"CLASS_DOMINANT", "CLASS_UNDERREPRESENTED"} & self.codes(report))

    def test_07_train_valid_leakage_is_error(self):
        shutil.copyfile(self.fixture.root / "train/images/train-a.jpg", self.fixture.root / "valid/images/valid-a.jpg")
        report = self.fixture.report()
        self.assertEqual(report["summary"]["status"], "ERROR")
        self.assertGreater(report["leakage"]["train_valid_duplicates"], 0)

    def test_08_train_test_leakage_is_error(self):
        shutil.copyfile(self.fixture.root / "train/images/train-a.jpg", self.fixture.root / "test/images/test-a.jpg")
        report = self.fixture.report()
        self.assertEqual(report["summary"]["status"], "ERROR")
        self.assertGreater(report["leakage"]["train_test_duplicates"], 0)

    def test_09_class_schema_change_requires_manual_review(self):
        previous = {
            "dataset_version": 30,
            "image_count": 3,
            "class_schema": [{"class_id": 0, "class_name": "helmet"}, {"class_id": 1, "class_name": "worker"}],
        }
        report = self.fixture.report(previous)
        self.assertEqual(report["summary"]["status"], "MANUAL_REVIEW")
        self.assertIn("CLASS_SCHEMA_CHANGED", self.codes(report))

    def test_10_dataset_size_drop_is_warning(self):
        previous = {
            "dataset_version": 30,
            "image_count": 100,
            "class_schema": [{"class_id": 0, "class_name": "helmet"}, {"class_id": 1, "class_name": "person"}],
        }
        report = self.fixture.report(previous)
        self.assertEqual(report["summary"]["status"], "WARNING")
        self.assertIn("DATASET_SIZE_CHANGED", self.codes(report))

    def test_11_small_images_are_warning(self):
        for split, stem, color in (("train", "train-a", (255, 0, 0)), ("valid", "valid-a", (0, 255, 0)), ("test", "test-a", (0, 0, 255))):
            Image.new("RGB", (100, 100), color).save(self.fixture.root / split / "images" / f"{stem}.jpg")
        report = self.fixture.report()
        self.assertEqual(report["summary"]["status"], "WARNING")
        self.assertIn("SMALL_IMAGE_RATIO_WARNING", self.codes(report))

    def test_12_within_split_exact_duplicate_is_warning(self):
        shutil.copyfile(self.fixture.root / "train/images/train-a.jpg", self.fixture.root / "train/images/train-copy.jpg")
        shutil.copyfile(self.fixture.root / "train/labels/train-a.txt", self.fixture.root / "train/labels/train-copy.txt")
        report = self.fixture.report()
        self.assertEqual(report["summary"]["status"], "WARNING")
        self.assertIn("DUPLICATE_IMAGES", self.codes(report))

    def test_13_bbox_quality_outliers_are_warning(self):
        cfg = policy()
        cfg["bounding_boxes"]["warning_ratio"] = 0.0
        (self.fixture.root / "train/labels/train-a.txt").write_text("0 0.5 0.5 0.0001 0.5\n")
        validate_dataset(self.fixture.root)
        report = analyze_dataset_quality(self.fixture.root, 31, cfg)
        self.assertIn("TINY_BBOX_RATIO_WARNING", self.codes(report))
        self.assertIn("EXTREME_ASPECT_RATIO_WARNING", self.codes(report))


if __name__ == "__main__":
    unittest.main()
