# -*- coding: utf-8 -*-
"""부스 인쇄용 QR 포스터 생성.

    python make_qr_poster.py --base https://163.152.155.234 --out poster.png

QR 의 목적지는 <base>/booth/start/ (README 8. 휴대폰으로 참여). 서버 주소가 바뀌면
이 스크립트로 다시 만들어 인쇄한다. A4 비율 300 dpi PNG 와 같은 이름의 PDF 를 쓴다.
필요: pip install qrcode pillow. 글꼴은 Windows 맑은 고딕을 쓰고, 없으면 --font 로 지정.
"""
import argparse
import os

import qrcode
from qrcode.constants import ERROR_CORRECT_H
from PIL import Image, ImageDraw, ImageFont

W, H = 2480, 3508          # A4 300 dpi
FONT_BOLD = 'C:/Windows/Fonts/malgunbd.ttf'
FONT_REG = 'C:/Windows/Fonts/malgun.ttf'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True, help='https://<서버주소> (끝 / 없이)')
    ap.add_argument('--out', default='booth_qr_poster.png')
    ap.add_argument('--title', default='WillSleep 수면 체험')
    ap.add_argument('--subtitle', default='휴대폰 카메라로 QR 코드를 찍어 주세요')
    ap.add_argument('--footer', default='입력하신 정보는 체험일로부터 14일 후 삭제됩니다')
    ap.add_argument('--font-bold', default=FONT_BOLD)
    ap.add_argument('--font', default=FONT_REG)
    args = ap.parse_args()

    url = args.base.rstrip('/') + '/booth/start/'
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=10, border=0)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color='black', back_color='white').convert('RGB')
    qr_img = qr_img.resize((1380, 1380), Image.NEAREST)

    page = Image.new('RGB', (W, H), 'white')
    draw = ImageDraw.Draw(page)
    title_font = ImageFont.truetype(args.font_bold, 170)
    sub_font = ImageFont.truetype(args.font, 90)
    foot_font = ImageFont.truetype(args.font, 60)

    def center(text, font, y, fill='black'):
        w = draw.textlength(text, font=font)
        draw.text(((W - w) / 2, y), text, font=font, fill=fill)

    center(args.title, title_font, 330)
    center(args.subtitle, sub_font, 590)
    page.paste(qr_img, ((W - 1380) // 2, 1060))
    center(args.footer, foot_font, 3180, fill='#777777')

    page.save(args.out, dpi=(300, 300))
    pdf = os.path.splitext(args.out)[0] + '.pdf'
    page.save(pdf, 'PDF', resolution=300)
    print('QR 대상:', url)
    print('저장:', args.out, '/', pdf)


if __name__ == '__main__':
    main()
