from pathlib import Path


def capabilities(printer: str) -> dict:
    import win32print
    handle = win32print.OpenPrinter(printer)
    try:
        info = win32print.GetPrinter(handle, 2)
        port = info['pPortName']
        papers = win32print.DeviceCapabilities(printer, port, 2)
        return {'name': printer, 'supports_color': win32print.DeviceCapabilities(printer, port, 32) == 1, 'supports_duplex': win32print.DeviceCapabilities(printer, port, 7) == 1, 'paper_sizes': [name for name, code in [('A4', 9), ('A3', 8)] if code in papers]}
    finally:
        win32print.ClosePrinter(handle)


def selected_pages(expression: str, count: int) -> list[int]:
    if expression.strip().lower() == 'all':
        return list(range(count))
    result = set()
    for part in expression.split(','):
        values = part.strip().split('-')
        start = int(values[0]); end = int(values[-1])
        if not 1 <= start <= end <= count:
            raise ValueError('Page range is outside the document')
        result.update(range(start - 1, end))
    if not result:
        raise ValueError('No pages selected')
    return sorted(result)


class WindowsPrinter:
    label = 'WINDOWS SPOOLER - physical completion unconfirmed'

    def __init__(self, printer):
        self.printer = printer

    def print_pdf(self, path: Path, options: dict) -> dict:
        import pymupdf
        import win32con
        import win32gui
        import win32print
        import win32ui
        from PIL import Image, ImageWin
        if not self.printer:
            raise RuntimeError('Select an installed printer')
        caps = capabilities(self.printer)
        if options['paper_size'] not in caps['paper_sizes']:
            raise RuntimeError('Selected paper size is not supported by this printer')
        if options['color_mode'] == 'color' and not caps['supports_color']:
            raise RuntimeError('This printer does not support color')
        if options['duplex'] and not caps['supports_duplex']:
            raise RuntimeError('This printer does not support duplex')
        handle = win32print.OpenPrinter(self.printer)
        dc = None
        started = False
        try:
            printer_info = win32print.GetPrinter(handle, 2)
            if 'PORTPROMPT' in printer_info['pPortName'].upper():
                raise RuntimeError('This virtual printer requires an interactive output filename; select an unattended physical printer')
            devmode = printer_info['pDevMode']
            if devmode is None:
                raise RuntimeError('Printer driver did not expose its settings')
            devmode.PaperSize = 9 if options['paper_size'] == 'A4' else 8
            devmode.Color = 2 if options['color_mode'] == 'color' else 1
            devmode.Duplex = 2 if options['duplex'] else 1
            devmode.Copies = 1
            devmode.Fields |= win32con.DM_PAPERSIZE | win32con.DM_COLOR | win32con.DM_DUPLEX | win32con.DM_COPIES
            result = win32print.DocumentProperties(0, handle, self.printer, devmode, devmode, win32con.DM_IN_BUFFER | win32con.DM_OUT_BUFFER)
            if result != 1 or devmode.PaperSize != (9 if options['paper_size'] == 'A4' else 8) or devmode.Duplex != (2 if options['duplex'] else 1) or devmode.Color != (2 if options['color_mode'] == 'color' else 1):
                raise RuntimeError('Printer driver rejected the requested settings')
            dc = win32ui.CreateDCFromHandle(win32gui.CreateDC('WINSPOOL', self.printer, devmode))
            with pymupdf.open(path) as pdf:
                if pdf.is_encrypted or len(pdf) < 1:
                    raise RuntimeError('PDF is encrypted or empty')
                pages = selected_pages(options['page_range'], len(pdf))
                job_id = dc.StartDoc('PrintSetu document')
                started = True
                for _ in range(options['copies']):
                    for index in pages:
                        page = pdf[index]
                        scale = min(2.0, 2200 / max(page.rect.width, page.rect.height))
                        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB if options['color_mode'] == 'color' else pymupdf.csGRAY, alpha=False)
                        image = Image.frombytes('RGB' if pix.n == 3 else 'L', (pix.width, pix.height), pix.samples).convert('RGB')
                        width = dc.GetDeviceCaps(win32con.HORZRES); height = dc.GetDeviceCaps(win32con.VERTRES)
                        ratio = min(width / image.width, height / image.height)
                        target_width = int(image.width * ratio); target_height = int(image.height * ratio)
                        left = (width - target_width) // 2; top = (height - target_height) // 2
                        dc.StartPage()
                        ImageWin.Dib(image).draw(dc.GetHandleOutput(), (left, top, left + target_width, top + target_height))
                        dc.EndPage()
                dc.EndDoc()
                started = False
            try:
                state = win32print.GetJob(handle, job_id, 1)['Status']
                if state & (win32print.JOB_STATUS_ERROR | win32print.JOB_STATUS_OFFLINE | win32print.JOB_STATUS_PAPEROUT):
                    raise RuntimeError(f'Spooler reports a printer error for job {job_id}; do not retry without checking the queue')
            except win32print.error as exc:
                if exc.winerror != 87:
                    raise
            return {'status': 'SUBMITTED_TO_SPOOLER', 'spooler_job_id': str(job_id)}
        finally:
            if dc:
                if started:
                    dc.AbortDoc()
                dc.DeleteDC()
            win32print.ClosePrinter(handle)
