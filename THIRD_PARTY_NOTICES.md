# Third-party notices

## Dietrich

Parts of `noteditor/sdocx_ink.py` are adapted from Dietrich's Samsung
Notes parser.

MIT License

Copyright (c) 2026 Dietrich contributors

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

## inkterop

`tests/fixtures/goodnotes/gn-mac-mixed-pens.goodnotes` is redistributed
unchanged from the inkterop project, where it is a controlled GoodNotes 6
(Mac App Store) export used as a format fixture. NotEditor's Goodnotes
support in `noteditor/goodnotes_*.py` is an independent implementation, but
the container layout, page/event model and geometry-signature facts it
relies on were established by inkterop's published reverse-engineering
notes (`docs/formats/goodnotes.md`). Those notes in turn credit
[franzthiemann/goodparse](https://github.com/franzthiemann/goodparse) for
first documenting the container layout, LZ4 framing and stroke-triplet
encoding; goodparse's source code was not read or reused here.

MIT License

Copyright (c) 2026 Caleb (cable729)

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

## goodnotes-pdf-engine

The reading of a Goodnotes page's background reference (paper record field 4
= attachment id, field 5 = page number inside that attachment) was
cross-checked against
[fakeminjun7321/goodnotes-pdf-engine](https://github.com/fakeminjun7321/goodnotes-pdf-engine)
(MIT), which is not a dependency of NotEditor.
