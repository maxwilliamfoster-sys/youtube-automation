"""
Visual fixes of 2026-10-13. The Danielle Imbo video (2026-10-12) opened on a festive-lit
station, then showed Amman (ancient "Philadelphia"), two brown rats and a sideways
photo of the Benjamin Franklin Parkway.

Run:  py -m unittest discover -s tests -v
No network, no LLM calls, nothing is posted.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import image_safety  # noqa: E402
import uk_media  # noqa: E402


class CommonsFilterTests(unittest.TestCase):
    def test_title_must_name_the_place(self):
        self.assertFalse(uk_media.names_place("Amman BW 2.JPG", "Philadelphia"))
        self.assertTrue(uk_media.names_place(
            "Benjamin Franklyn Parkway in Philadelphia, 2-18-18.jpg", "Philadelphia"))
        self.assertTrue(uk_media.names_place(
            "Hemdean Road from Priest Hill, Reading - geograph.org.uk - 6.jpg",
            "Hemdean Road, Caversham"))

    def test_generic_words_do_not_count(self):
        self.assertFalse(uk_media.names_place("Road works in Leeds.jpg", "Hemdean Road"))

    def test_off_mood_subjects_blocked(self):
        for title in ["Brown Rat (Rattus norvegicus) also called Norway Rat, Philadelphia.jpg",
                      "Arthur Louis Townsend (1848-1927) - Catmint - Ipswich Museums.jpg",
                      "Morrisons, Bawtry Road, Bramley, Rotherham - geograph.org.uk.jpg",
                      "Ibis Rotherham East, Moorhead Way.jpg"]:
            self.assertTrue(uk_media._WRONG_PLACE.search(title), title)

    def test_street_photos_pass(self):
        for title in ["Rotherham - Rotherham Lock - geograph.org.uk - 2283308.jpg",
                      "Ovingdean - geograph.org.uk - 3713578.jpg",
                      "City of Philadelphia from the top.jpg"]:
            self.assertFalse(uk_media._WRONG_PLACE.search(title), title)

    def test_wrong_region_for_reused_place_names(self):
        # Brianna Maitland (Montgomery, Vermont) got Montgomery, Alabama on 2026-10-12.
        wr = uk_media.wrong_region
        self.assertTrue(wr("Greetings from Montgomery, Alabama - Large Letter.jpg", "Montgomery, Vermont"))
        self.assertTrue(wr("Ballpark of Vermont, Illinois.jpg", "Montgomery, Vermont"))
        self.assertFalse(wr("Church Street, Burlington, Vermont United States.jpg", "Montgomery, Vermont"))
        self.assertFalse(wr("Charleston, West Virginia skyline.jpg", "Charleston, West Virginia"))
        self.assertFalse(wr("Ipswich docks, Suffolk, England - geograph.jpg", "Ipswich, Suffolk", uk=True))
        self.assertTrue(wr("Ipswich, Queensland street.jpg", "Ipswich, Suffolk", uk=True))

    def test_geograph_ranked_first_and_wrong_place_dropped(self):
        found = {"query": {"search": [{"title": "File:a.jpg"}]}}
        details = [
            {"title": "Ipswich town hall.jpg"},
            {"title": "Amman BW 2.JPG"},
            {"title": "Ipswich docks - geograph.org.uk - 1.jpg"},
        ]
        with mock.patch.object(uk_media, "_get", return_value=found), \
                mock.patch.object(uk_media, "_image_details", return_value=details):
            out = uk_media.location_images("Ipswich dark street", uk=True, must_name="Ipswich")
        self.assertEqual([d["title"] for d in out],
                         ["Ipswich docks - geograph.org.uk - 1.jpg", "Ipswich town hall.jpg"])


class PrivacyTests(unittest.TestCase):
    """Audit 2026-10-21 F11: the Kitandwe video showed a private cottage and a legible
    number plate in the wrong town because 'Wayford' alone matched the street name."""

    def test_street_needs_full_phrase_or_street_plus_locality(self):
        np = uk_media.names_place
        self.assertFalse(np("House and driveway, Wayford - geograph", "Wayford Street, Battersea, London"))
        self.assertFalse(np("Wayford village green - geograph.jpg", "Wayford Street",
                            locality="Wayford Street, Battersea, London"))
        self.assertTrue(np("Wayford Street, SW11 - geograph.jpg", "Wayford Street",
                           locality="Wayford Street, Battersea, London"))
        self.assertTrue(np("Wayford St looking north.jpg", "Wayford Street"))
        self.assertTrue(np("Wayford, Battersea - geograph.jpg", "Wayford Street",
                           locality="Wayford Street, Battersea, London"))
        self.assertTrue(np("Well_Hall_Road,_Eltham.jpg", "Well Hall Road, Eltham"))
        self.assertFalse(np("Well Hall Pleasaunce.jpg", "Well Hall Road"))

    def test_private_property_titles_rejected(self):
        for title in ["House and driveway, Wayford - geograph",
                      "Houses on Well Hall Road - geograph.org.uk.jpg",
                      "Cottage at Moorlinch.jpg", "Cars parked on Wayford Street.jpg",
                      "A van in Battersea.jpg", "Home of the Smith family, Leeds.jpg",
                      "Farmhouse near Bridgwater.jpg", "Bungalow, Caversham.jpg",
                      "Parked car, Eltham.jpg", "Vehicles on Wayford Street.jpg"]:
            self.assertTrue(uk_media.private_property(title), title)
        for title in ["Wayford Street sign.jpg", "Clapham Junction station - geograph.jpg",
                      "St Mary's Church, Battersea.jpg", "Old Bailey, London.jpg",
                      "Carlisle Castle.jpg", "Battersea Park.jpg"]:
            self.assertFalse(uk_media.private_property(title), title)

    def test_location_images_drops_house_photo_in_wrong_town(self):
        found = {"query": {"search": [{"title": "File:a.jpg"}]}}
        details = [{"title": "House and driveway, Wayford - geograph"},
                   {"title": "Wayford Street, Battersea - geograph.jpg"},
                   {"title": "Wayford church - geograph.jpg"}]
        with mock.patch.object(uk_media, "_get", return_value=found), \
                mock.patch.object(uk_media, "_image_details", return_value=details):
            out = uk_media.location_images("Wayford Street Battersea", uk=True,
                                           must_name="Wayford Street",
                                           case_place="Wayford Street, Battersea, London")
        self.assertEqual([d["title"] for d in out], ["Wayford Street, Battersea - geograph.jpg"])

    def test_street_search_is_anchored_to_district(self):
        import image_generator as ig
        queries = []

        def fake_loc(query, **kw):
            queries.append((query, kw.get("must_name")))
            return []

        with mock.patch.object(uk_media, "location_images", side_effect=fake_loc):
            ok = ig._uk_location_image("Wayford Street, Battersea, London", "night",
                                       "x.jpg", set(), [])
        self.assertFalse(ok)                      # nothing matched -> caller uses stock
        street_q = [q for q, m in queries if m == "Wayford Street"]
        self.assertTrue(street_q)
        self.assertTrue(all("Battersea" in q for q in street_q), street_q)

    def test_ai_gate_prompt_blocks_private_homes_and_plates(self):
        import inspect
        import upload_gate
        src = inspect.getsource(upload_gate.ai_review)
        self.assertIn("private home", src)
        self.assertIn("number plate", src)


class MoodGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _img(self, name, colour):
        from PIL import Image
        p = os.path.join(self.tmp.name, name)
        Image.new("RGB", (400, 300), colour).save(p)
        return p

    def test_festive_colour_refused(self):
        self.assertNotEqual(image_safety.too_cheerful_reason(self._img("pink.jpg", (230, 40, 200))), "")

    def test_grey_street_passes(self):
        self.assertEqual(image_safety.too_cheerful_reason(self._img("grey.jpg", (110, 112, 115))), "")

    def test_dark_saturated_passes(self):
        # A deep night-blue sky is saturated but not bright: fine for the mood.
        self.assertEqual(image_safety.too_cheerful_reason(self._img("night.jpg", (10, 20, 70))), "")


class ExifRotationTests(unittest.TestCase):
    def test_sideways_original_is_turned_upright(self):
        """A landscape-stored file with EXIF Orientation=6 is a portrait photo."""
        from PIL import Image
        import documentary_composer as dc
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "sideways.jpg")
            # Stored 400 wide x 200 tall: left half red, right half blue.
            im = Image.new("RGB", (400, 200), (200, 0, 0))
            im.paste((0, 0, 200), (200, 0, 400, 200))
            exif = im.getexif()
            exif[274] = 6                       # rotate 90 CW to display
            im.save(src, exif=exif.tobytes())
            out = os.path.join(tmp, "clip.mp4")
            dc.make_ken_burns_clip(src, 0.2, 0, out)   # style 0: centre push-in
            frame = os.path.join(tmp, "f.png")
            subprocess.run([dc.FFMPEG, "-y", "-i", out, "-frames:v", "1", frame],
                           capture_output=True, check=True)
            got = Image.open(frame).convert("RGB")
            w, h = got.size
            top, bottom = got.getpixel((w // 2, h // 6)), got.getpixel((w // 2, 5 * h // 6))
        # Upright, the red half is on top and the blue half below. Without the EXIF
        # transpose the 9:16 crop would sit across the red/blue boundary sideways.
        self.assertGreater(top[0], top[2])
        self.assertGreater(bottom[2], bottom[0])


class SceneMixTests(unittest.TestCase):
    def test_hook_scene_never_uses_place_photo(self):
        import image_generator as ig
        calls = []

        def fake_place(place, q, out, seen, manifest, uk=True):
            calls.append(out)
            from PIL import Image
            Image.new("RGB", (1080, 1920), (60, 60, 60)).save(out)
            return True

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(ig, "_uk_location_image", side_effect=fake_place), \
                mock.patch.object(ig, "UK_LOCATION_IMAGES", True), \
                mock.patch.object(ig, "PEXELS_API_KEY", "x"), \
                mock.patch.object(ig, "_generate_pexels_queries", return_value=["q"] * 6), \
                mock.patch.object(ig, "fetch_pexels_video", return_value=False), \
                mock.patch.object(ig, "fetch_pexels_image", return_value=True):
            ig.generate_story_images("t", "one two three four five six " * 10, tmp,
                                     uk_place="Ipswich", num_images=6)
        used = sorted(os.path.basename(c) for c in calls)
        self.assertEqual(used, ["scene_03.jpg", "scene_06.jpg"])


if __name__ == "__main__":
    unittest.main()
