# The application only decodes Pixiv JPEG/PNG frames and exports GIF.
# Do not collect unrelated codec plugins or their native libraries.
hiddenimports = ["PIL.JpegImagePlugin", "PIL.PngImagePlugin", "PIL.GifImagePlugin"]
