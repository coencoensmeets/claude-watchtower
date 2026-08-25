/* The one dependency the panel loads that is not its own source.

   `web/assets/vendor/material-color-utilities.js` is a checked-in copy of
   Google's library, served straight to the browser from an absolute URL — so
   the import specifier is a URL and not a path any resolver can follow to a
   file. Hence a declaration by that exact specifier.

   Only what the panel actually calls is declared. Adding a call means adding
   it here too, which is the point: the checked-in copy is a fixed API surface,
   and a call this file does not know about is a call worth looking twice at.
*/

/** A colour in hue/chroma/tone space. */
export class Hct {
  static fromInt(argb: number): Hct;
  static from(hue: number, chroma: number, tone: number): Hct;
  readonly hue: number;
  readonly chroma: number;
  readonly tone: number;
  toInt(): number;
}

/** One hue and chroma, addressable at any tone. */
export class TonalPalette {
  static fromInt(argb: number): TonalPalette;
  static fromHueAndChroma(hue: number, chroma: number): TonalPalette;
  tone(tone: number): number;
}

/** The scheme the panel builds its --md-sys-color-* roles from. Every role
    is read by name off the instance, so it is indexable. */
export class DynamicScheme {
  readonly [role: string]: number;
}

export class SchemeVibrant extends DynamicScheme {
  constructor(sourceColorHct: Hct, isDark: boolean, contrastLevel: number);
}

export function argbFromHex(hex: string): number;
export function hexFromArgb(argb: number): string;
