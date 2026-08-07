import os
from PIL import Image, ImageDraw, ImageFont

def create_card():
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

    # 4. 폰트 설정 (맑은 고딕 / 예외시 기본 폰트)
    try:
        title_font = ImageFont.truetype("malgun.ttf", 48)
        subtitle_font = ImageFont.truetype("malgunbd.ttf", 28)
        body_font = ImageFont.truetype("malgun.ttf", 24)
    except IOError:
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()
        body_font = ImageFont.load_default()

    # 5. 텍스트 및 UI 레이아웃 배치
    # 상단 태그
    draw.text((margin + 40, margin + 50), "[QUANT SIGNAL UPDATE]", font=subtitle_font, fill=accent_mint)

    # 메인 헤드라인
    draw.text((margin + 40, margin + 110), "월가 퀀트 알고리즘\n오늘자 수급 포착 종목", font=title_font, fill=text_white)

    # 구분선
    draw.line([(margin + 40, margin + 250), (width - margin - 40, margin + 250)], fill=accent_mint, width=2)

    # 본문 텍스트
    sample_body = (
        "• 포착 시스템: 알파봇 v2.4\n"
        "• 포착 시각: 장 시작 15분 전\n"
        "• 세력 순매수 잔량: 상위 0.1% 돌파\n\n"
        "감으로 하는 매매는 끝났습니다.\n"
        "실시간 시그널은 디스코드 채널에서 확인하세요."
    )
    draw.text((margin + 40, margin + 290), sample_body, font=body_font, fill=text_gray)

    # 하단 CTA 박스
    draw.rectangle([margin + 40, height - margin - 120, width - margin - 40, height - margin - 40], fill=(15, 23, 42), outline=accent_gold, width=2)
    draw.text((margin + 60, height - margin - 95), "💡 댓글에 '스탁봇' 입력 시 무료 초대장 발송", font=subtitle_font, fill=accent_gold)

    # 6. 이미지 저장
    output_filename = "generate_card_output.png"
    img.save(output_filename)
    print(f"✅ 카드 생성 완료! 저장된 파일: {os.path.abspath(output_filename)}")

if __name__ == "__main__":
    create_card()