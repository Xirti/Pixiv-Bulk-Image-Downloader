# The application only decodes Pixiv JPEG/PNG frames and exports GIF.
# Do not collect unrelated codec plugins or their native libraries.
hiddenimports = ["PIL.JpegImagePlugin", "PIL.PngImagePlugin", "PIL.GifImagePlugin"]
# Image's optional array interoperability is not used by local conversion.
excludedimports = ["numpy", "tkinter"]
