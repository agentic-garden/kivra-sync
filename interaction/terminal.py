"""Display BankID's refreshing QR directly in the terminal."""

import shutil
import sys

import qrcode

from interaction.local import LocalInteractionProvider


class TerminalInteractionProvider(LocalInteractionProvider):
    supports_qr_refresh = True

    def display_qr_payload(self, payload):
        qr = qrcode.QRCode(border=4, box_size=1)
        qr.add_data(payload)
        qr.make(fit=True)
        matrix = qr.get_matrix()

        output = sys.stdout
        if output.encoding and output.encoding.lower().replace('-', '') != 'utf8':
            output.reconfigure(encoding='utf-8')

        interactive = output.isatty()
        if interactive:
            width = len(matrix[0]) * 2
            size = shutil.get_terminal_size()
            if width >= size.columns:
                raise RuntimeError(f'Terminal QR needs at least {width + 1} columns; current width is {size.columns}')
            # Authentication messages need room below the first frame.
            required_rows = len(matrix) + 5
            if required_rows > size.lines:
                raise RuntimeError(f'Terminal QR needs at least {required_rows} rows; current height is {size.lines}')
            output.write('\x1b[2J\x1b[H\x1b[30;47m')
        else:
            output.write('\n')

        for row in matrix:
            output.write(''.join('██' if module else '  ' for module in row) + '\n')

        if interactive:
            output.write('\x1b[0m')
        output.flush()
