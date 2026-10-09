"""
MIT License

Copyright (c) 2025

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

"""
Model of how a TRV maps a temperature shortfall to a valve opening.

A held zone has its valve forced open by a calibration offset, so its measured opening says
nothing about how much heat the room wants. Its *virtual* opening is derived from the room
temperature error instead, using the curve below, so the rest of the controller treats it
like any other valve.

To change how hard a held zone asks for heat, edit OPENING_CURVE only.
"""

# (error in °C at or above which the opening applies, opening in %), ascending.
# A positive error below the first threshold still asks for the first step: the room is
# below target, so the held valve should request a little heat.
OPENING_CURVE: tuple[tuple[float, float], ...] = (
    (0.5, 25.0),
    (1.0, 50.0),
    (1.5, 75.0),
    (2.0, 100.0),
)


def opening_for_error(error: float) -> float:
    """Virtual valve opening (%) for a temperature error (target - current, °C)."""
    if error <= 0.0:
        return 0.0
    opening = OPENING_CURVE[0][1]
    for threshold, curve_opening in OPENING_CURVE:
        if error >= threshold:
            opening = curve_opening
    return opening
