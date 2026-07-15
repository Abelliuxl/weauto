import numpy as np

from wechat_rpa.visible_message_parser import VisibleMessageParser


def test_detect_blocks_keeps_large_text_bubbles_as_text_not_images():
    image = np.full((1800, 720, 3), 247, dtype=np.uint8)

    # Real image-like content: colorful and not dominated by WeChat bubble fill.
    # The first block is a wide self image: its left edge sits far from the right
    # side, so a plain x-threshold would incorrectly mark it as incoming.
    y_grid, x_grid = np.indices((248, 370))
    image[220:468, 206:576, 0] = (x_grid % 180) + 20
    image[220:468, 206:576, 1] = (y_grid % 160) + 40
    image[220:468, 206:576, 2] = ((x_grid + y_grid) % 180) + 30
    image[478:550, 604:676] = (60, 70, 80)

    other_y_grid, other_x_grid = np.indices((180, 300))
    image[610:790, 130:430, 0] = (other_x_grid % 180) + 20
    image[610:790, 130:430, 1] = (other_y_grid % 160) + 40
    image[610:790, 130:430, 2] = ((other_x_grid + other_y_grid) % 180) + 30
    image[610:682, 40:112] = (60, 70, 80)

    # Large self and incoming text bubbles. These used to be misclassified as images.
    image[888:1082, 178:586] = (120, 235, 150)
    image[1122:1396, 130:546] = (238, 238, 238)

    parser = VisibleMessageParser(ocr_engine=None)  # type: ignore[arg-type]
    blocks = parser._detect_blocks(image, body_y1=190, body_y2=1590)

    assert [(block.kind, block.side, block.bbox) for block in blocks] == [
        ("image", "self", [206, 220, 576, 468]),
        ("image", "other", [130, 610, 430, 790]),
        ("text", "self", [178, 888, 586, 1082]),
        ("text", "other", [130, 1122, 546, 1396]),
    ]


def test_detect_blocks_supports_windows_short_bubbles_and_rich_card_near_input():
    image = np.full((971, 919, 3), 250, dtype=np.uint8)
    image[199:240, 109:215] = (238, 238, 240)
    image[362:416, 109:194] = (238, 238, 240)
    image[539:738, 109:487] = (238, 238, 240)
    # Input panel top border, close enough that an unfiltered merge swallows
    # the rich card into an invalid full-width block.
    image[750:761, 22:895] = (243, 243, 243)

    body_y1, body_y2 = VisibleMessageParser._chat_body_bounds(image.shape[0])
    blocks = VisibleMessageParser._detect_blocks(
        image,
        body_y1=body_y1,
        body_y2=body_y2,
    )

    assert (body_y1, body_y2) == (115, 761)
    assert [(block.kind, block.side, block.bbox) for block in blocks] == [
        ("text", "other", [109, 199, 215, 240]),
        ("text", "other", [109, 362, 194, 416]),
        ("text", "other", [109, 539, 487, 738]),
    ]
