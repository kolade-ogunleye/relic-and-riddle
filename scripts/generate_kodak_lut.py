"""
scripts/generate_kodak_lut.py
=============================
Generates an authentic 33x33x33 3D LUT (.cube) emulating Kodak 2383 cinema film print stock.
- Soft-knee toe (lifts subterranean blacks above digital 0-clipping)
- Warm golden/amber midtone response
- Cool cyan subterranean shadow split-toning
- Soft shoulder highlight roll-off
"""

from pathlib import Path
import numpy as np


def generate_kodak_2383_cube(out_path: Path, size: int = 33):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    coords = np.linspace(0.0, 1.0, size)

    def film_s_curve(x):
        toe = 0.018
        shoulder = 0.965
        s = 1.0 / (1.0 + np.exp(-10.0 * (x - 0.48)))
        s0 = 1.0 / (1.0 + np.exp(-10.0 * (0.0 - 0.48)))
        s1 = 1.0 / (1.0 + np.exp(-10.0 * (1.0 - 0.48)))
        norm_s = (s - s0) / (s1 - s0)
        blended = 0.65 * norm_s + 0.35 * x
        return toe + (shoulder - toe) * blended

    lines = [
        'TITLE "Kodak 2383 Film Print Emulation"',
        f"LUT_3D_SIZE {size}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]

    for b in coords:
        for g in coords:
            for r in coords:
                lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
                r_c = film_s_curve(r)
                g_c = film_s_curve(g)
                b_c = film_s_curve(b)

                warmth = np.sin(np.pi * lum) * 0.025
                cool_shadow = max(0.0, 0.35 - lum) * 0.045

                r_out = np.clip(r_c + warmth * 1.0 - cool_shadow * 0.4, 0.0, 1.0)
                g_out = np.clip(g_c + warmth * 0.3 + cool_shadow * 0.2, 0.0, 1.0)
                b_out = np.clip(b_c - warmth * 0.7 + cool_shadow * 0.8, 0.0, 1.0)

                lines.append(f"{r_out:.6f} {g_out:.6f} {b_out:.6f}")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


if __name__ == "__main__":
    p = Path("assets/luts/kodak_2383.cube")
    generate_kodak_2383_cube(p)
    print(f"Generated {p} successfully, size: {p.stat().st_size} bytes")
