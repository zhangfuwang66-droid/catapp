# -*- coding: utf-8 -*-
"""
创建简单的应用图标
使用 PIL 生成一个猫咪主题的图标
"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

def create_cat_icon():
    """创建猫咪图标"""
    # 创建 256x256 的图像
    size = 256
    img = Image.new('RGBA', (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    # 背景圆形（深蓝色）
    circle_margin = 10
    draw.ellipse([circle_margin, circle_margin, size - circle_margin, size - circle_margin],
                 fill=(41, 128, 185, 255))

    # 猫咪头部（白色）
    head_size = 120
    head_x = (size - head_size) // 2
    head_y = 50
    draw.ellipse([head_x, head_y, head_x + head_size, head_y + head_size],
                 fill=(255, 255, 255, 255))

    # 左耳朵
    ear_points = [(head_x + 20, head_y + 10), (head_x + 10, head_y - 30), (head_x + 50, head_y + 20)]
    draw.polygon(ear_points, fill=(255, 255, 255, 255))

    # 右耳朵
    ear_points = [(head_x + head_size - 20, head_y + 10),
                  (head_x + head_size - 10, head_y - 30),
                  (head_x + head_size - 50, head_y + 20)]
    draw.polygon(ear_points, fill=(255, 255, 255, 255))

    # 左眼睛
    eye_size = 15
    left_eye_x = head_x + 30
    eye_y = head_y + 40
    draw.ellipse([left_eye_x, eye_y, left_eye_x + eye_size, eye_y + eye_size],
                 fill=(52, 73, 94, 255))

    # 右眼睛
    right_eye_x = head_x + head_size - 30 - eye_size
    draw.ellipse([right_eye_x, eye_y, right_eye_x + eye_size, eye_y + eye_size],
                 fill=(52, 73, 94, 255))

    # 鼻子（粉色三角形）
    nose_x = head_x + head_size // 2
    nose_y = head_y + 65
    nose_points = [(nose_x, nose_y), (nose_x - 8, nose_y - 10), (nose_x + 8, nose_y - 10)]
    draw.polygon(nose_points, fill=(231, 76, 60, 255))

    # 嘴巴
    mouth_y = nose_y + 5
    draw.arc([nose_x - 15, mouth_y, nose_x, mouth_y + 15], 0, 180, fill=(52, 73, 94, 255), width=2)
    draw.arc([nose_x, mouth_y, nose_x + 15, mouth_y + 15], 0, 180, fill=(52, 73, 94, 255), width=2)

    # 文字 "AI"
    try:
        font_size = 40
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except:
            font = ImageFont.load_default()

        text = "AI"
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        text_x = (size - text_width) // 2
        text_y = head_y + head_size + 10

        # 文字阴影
        draw.text((text_x + 2, text_y + 2), text, fill=(0, 0, 0, 128), font=font)
        # 文字主体
        draw.text((text_x, text_y), text, fill=(255, 255, 255, 255), font=font)
    except Exception as e:
        print(f"警告: 无法添加文字 - {e}")

    return img

def save_icon_sizes(img, output_path):
    """保存多个尺寸的图标到 ICO 文件"""
    # ICO 格式需要多个尺寸
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    images = []

    for size in sizes:
        resized = img.resize(size, Image.Resampling.LANCZOS)
        images.append(resized)

    # 保存为 ICO
    images[0].save(output_path, format='ICO', sizes=[(img.width, img.height) for img in images],
                   append_images=images[1:])
    print(f"✅ 图标已保存: {output_path}")

def main():
    """主函数"""
    assets_dir = Path(__file__).parent / 'assets'
    assets_dir.mkdir(exist_ok=True)

    print("🎨 正在生成应用图标...")
    icon_img = create_cat_icon()

    # 保存 PNG（用于预览）
    png_path = assets_dir / 'icon.png'
    icon_img.save(png_path, 'PNG')
    print(f"✅ PNG 图标已保存: {png_path}")

    # 保存 ICO（用于 EXE）
    ico_path = assets_dir / 'icon.ico'
    save_icon_sizes(icon_img, ico_path)

    print("\n🎉 图标创建完成！")

if __name__ == '__main__':
    main()
