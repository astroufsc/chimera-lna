# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy"]
# ///
# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Fit the DomeGeometry parameters to a dome mapping run.

Input: a CSV like data/model.csv with columns ALT, AZ, HA (radians) and
ETIQUETA (the dome tag the operator centered the slit on for that pointing).
Output: the fitted parameter block to paste into the DomeLNA __config__.

The pier side of each point starts as sign(HA) and is then reassigned to
whichever side the model predicts better (a mapping run tracking past the
meridian sits on the "wrong" side for its HA); points farther than 5 sigma
transverse to the slit are dropped as table errors.

Run: uv run scripts/fit_dome_geometry.py [path/to/model.csv]
"""

import sys
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

LATITUDE = -(22 + 32 / 60 + 4 / 3600)


def tag_to_az(tag):
    return np.where(tag < 846, 270 + (tag - 801) * 2, (tag - 846) * 2) % 360


def load(path):
    raw = np.genfromtxt(
        path,
        delimiter=",",
        names=True,
        dtype=None,
        encoding="utf-8",
        invalid_raise=False,
    )
    alt = np.asarray(raw["ALT"], float)
    az = np.asarray(raw["AZ"], float)
    ha = np.asarray(raw["HA"], float)
    tag = np.asarray(raw["ETIQUETA"], float)
    good = ~((alt == 0) & (az == 0) & (tag == 0)) & np.isfinite(alt) & np.isfinite(tag)
    # the run logs HA in [-2pi, 0]: wrap to [-pi, pi] before taking its sign
    ha = np.arctan2(np.sin(ha), np.cos(ha))
    return alt[good], az[good], ha[good], tag[good]


def unit_vectors(alt, az, latitude):
    phi = np.radians(latitude)
    p = np.stack(
        [np.sin(az) * np.cos(alt), np.cos(az) * np.cos(alt), np.sin(alt)], axis=1
    )
    polar = np.array([0.0, np.cos(phi), np.sin(phi)])
    if phi < 0:
        polar = -polar
    d = np.cross(np.broadcast_to(polar, p.shape), p)
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    return p, d


def predict(params, p, d, side):
    x, y, z, r, dphi = params
    tube = np.array([x, y, z]) + (side * r)[:, None] * d
    tp = np.einsum("ij,ij->i", tube, p)
    disc = tp**2 + 1 - np.einsum("ij,ij->i", tube, tube)
    t = -tp + np.sqrt(np.maximum(disc, 1e-9))
    hit = tube + t[:, None] * p
    az_pred = (np.degrees(np.arctan2(hit[:, 0], hit[:, 1])) + dphi) % 360
    elev = np.degrees(np.arcsin(np.clip(hit[:, 2], -1, 1)))
    return az_pred, elev


def wrap(x):
    return (x + 180) % 360 - 180


def main():
    path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else Path(__file__).parent.parent / "src/chimera_lna/data/model.csv"
    )
    alt, az, ha, tag = load(path)
    obs = tag_to_az(tag)
    p, d = unit_vectors(alt, az, LATITUDE)
    print(f"{len(alt)} points from {path}")

    mask = np.ones(len(alt), bool)
    side = np.where(np.sign(ha) == 0, 1.0, np.sign(ha))
    params = np.array([0.0, 0.0, 0.0, 0.1, 0.0])

    for _ in range(6):
        for _ in range(20):

            def resid(q):
                azp, _ = predict(q, p[mask], d[mask], side[mask])
                return wrap(azp - obs[mask])

            params = least_squares(resid, params, loss="soft_l1", f_scale=1.5).x
            azp_p, _ = predict(params, p, d, np.ones(len(alt)))
            azp_m, _ = predict(params, p, d, -np.ones(len(alt)))
            new_side = np.where(
                np.abs(wrap(azp_p - obs)) <= np.abs(wrap(azp_m - obs)), 1.0, -1.0
            )
            done = np.all(new_side[mask] == side[mask])
            side = new_side
            if done:
                break
        azp, elev = predict(params, p, d, side)
        slit_miss = wrap(azp - obs) * np.cos(np.radians(elev))
        sigma = 1.4826 * np.median(np.abs(slit_miss - np.median(slit_miss)))
        new_mask = np.abs(slit_miss) < max(5 * sigma, 5.0)
        if np.all(new_mask == mask):
            break
        mask = new_mask

    azp, elev = predict(params, p, d, side)
    r = wrap(azp - obs)[mask]
    rw = (wrap(azp - obs) * np.cos(np.radians(elev)))[mask]
    print(f"kept {mask.sum()}/{len(mask)} points")
    for i in np.where(~mask)[0]:
        print(
            f"  dropped: alt={np.degrees(alt[i]):5.1f} az={np.degrees(az[i]):6.1f}"
            f" tag={int(tag[i])} residual={wrap(azp - obs)[i]:+.1f} deg"
        )
    print(
        f"azimuth residual: median {np.median(np.abs(r)):.2f} deg, "
        f"rms {np.sqrt(np.mean(r**2)):.2f} deg"
    )
    print(
        f"transverse to slit: median {np.median(np.abs(rw)):.2f} deg, "
        f"max {np.max(np.abs(rw)):.2f} deg"
    )
    print("\nDomeLNA __config__ block:")
    print(f'        "latitude": {LATITUDE:.4f},')
    print(f'        "mount_offset_east": {params[0]:.4f},')
    print(f'        "mount_offset_north": {params[1]:.4f},')
    print(f'        "mount_offset_up": {params[2]:.4f},')
    print(f'        "gem_offset": {params[3]:.4f},')
    print(f'        "dome_az_offset": {params[4]:.2f},')


if __name__ == "__main__":
    main()
