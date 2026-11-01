"""Exercise the local review page in installed Edge; no server or network needed."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent


def check():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto((HERE / "index.html").as_uri())
        page.evaluate("document.fonts.ready")
        assert page.locator("#gallery article").count() == 4
        combinations = 0
        for topic in ("richat", "gumbaz_exterior", "derinkuyu", "gumbaz"):
            page.select_option("#topic", topic)
            for phase in ("before", "after"):
                page.locator(f"#phase button[data-value={phase}]").click()
                for crop in ("full", "square", "portrait"):
                    page.locator(f"#crop button[data-value={crop}]").click()
                    for scale in ("fit", "phone"):
                        page.locator(f"#scale button[data-value={scale}]").click()
                        page.wait_for_function("Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)")
                        assert page.locator("#gallery").get_attribute("data-crop") == crop
                        assert page.locator("#gallery").get_attribute("data-scale") == scale
                        if scale == "phone":
                            widths = page.locator("#gallery .aperture").evaluate_all("els=>els.map(e=>e.getBoundingClientRect().width)")
                            assert widths == [180] * 4, widths
                        combinations += 1
            for style in ("badge", "minimal", "lower_third", "depth_sandwich"):
                page.select_option("#compare-style", style)
                page.wait_for_function("Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)")
        page.select_option("#topic", "gumbaz_exterior")
        page.select_option("#compare-style", "badge")
        page.locator("#scale button[data-value=fit]").click()
        page.locator("#crop button[data-value=square]").click()
        page.screenshot(path=str(HERE / "gallery-desktop.png"), full_page=True)
        page.locator("#scale button[data-value=phone]").click()
        page.screenshot(path=str(HERE / "gallery-phone-size.png"), full_page=True)
        for width in (390, 768):
            page.set_viewport_size({"width": width, "height": 844})
            for scale in ("fit", "phone"):
                page.locator(f"#scale button[data-value={scale}]").click()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, scale)
            if width == 390:
                page.screenshot(path=str(HERE / "gallery-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
    result = {"browser": "Microsoft Edge, headless", "combinations": combinations,
              "comparison_pairs": 16, "script_errors": errors,
              "phone_image_width": 180, "responsive_widths": [390, 768, 1440],
              "horizontal_overflow": False}
    (HERE / "gallery-validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    check()
