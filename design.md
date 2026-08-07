import os
from PIL import Image, ImageDraw, ImageFont

def get_text_size(draw, text, font):
    """Pillow 최신 버전 호환 텍스트 크기 측정 함수"""
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    return width, height

def generate_card(title, subtitle, body, output_filename="generate_card_output.png"):
    # 1. Canvas 설정 (1080x1350, design.md 규격)
    width, height = 1080, 1350
    bg_color = (10, 17, 40)       # #0A1128 Deep Navy
    card_bg = (30, 41, 59)        # #1E293B Dark Slate
    accent_mint = (0, 230, 118)    # #00E676 Neon Mint
    accent_gold = (255, 215, 0)    # #FFD700 Imperial Gold
    text_white = (255, 255, 255)   # #FFFFFF
    text_gray = (148, 163, 184)    # #94A3B8

    img = Image.new("RGB", (width, height), color=bg_color)
    draw = ImageDraw.Draw(img)

    # 2. 여백 규칙 (80px Outer Margin)
    margin = 80

    # 3. 내부 카드 배경 그리기
    card_box = [margin, margin, width - margin, height - margin]
    draw.rectangle(card_box, fill=card_bg, outline=accent_mint, width=3)

    # 4. 폰트 설정
    try:
        title_font = ImageFont.truetype("malgun.ttf", 48)
        subtitle_font = ImageFont.truetype("malgunbd.ttf", 28)
        body_font = ImageFont.truetype("malgun.ttf", 24)
    except IOError:
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()
        body_font = ImageFont.load_default()

    # 5. 텍스트 배치
    # 상단 태그
    draw.text((margin + 40, margin + 50), f"[{subtitle}]", font=subtitle_font, fill=accent_mint)

    # 메인 헤드라인
    draw.text((margin + 40, margin + 110), title, font=title_font, fill=text_white)

    # 구분선
    draw.line([(margin + 40, margin + 250), (width - margin - 40, margin + 250)], fill=accent_mint, width=2)

    # 본문 텍스트
    draw.text((margin + 40, margin + 290), body, font=body_font, fill=text_gray)

    # 하단 CTA 박스
    draw.rectangle([margin + 40, height - margin - 120, width - margin - 40, height - margin - 40], fill=(15, 23, 42), outline=accent_gold, width=2)
    draw.text((margin + 60, height - margin - 95), "💡 댓글에 '스탁봇' 입력 시 무료 초대장 발송", font=subtitle_font, fill=accent_gold)

    # 6. 이미지 저장
    img.save(output_filename)
    print(f"✅ 카드 생성 완료! 저장된 파일: {os.path.abspath(output_filename)}")

if __name__ == "__main__":
    sample_title = "월가 퀀트 알고리즘\n오늘자 수급 포착 종목"
    sample_subtitle = "QUANT SIGNAL UPDATE"
    sample_body = (
        "• 포착 시스템: 알파봇 v2.4\n"
        "• 포착 시각: 장 시작 15분 전\n"
        "• 세력 순매수 잔량: 상위 0.1% 돌파\n\n"
        "감으로 하는 매매는 끝났습니다.\n"
        "실시간 시그널은 디스코드 채널에서 확인하세요."
    )
    generate_card(sample_title, sample_subtitle, sample_body, "generate_card_output.png")