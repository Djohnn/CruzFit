from collections.abc import Iterable

from django.utils.safestring import mark_safe

_EAN13_L_CODES = (
    "0001101", "0011001", "0010011", "0111101", "0100011",
    "0110001", "0101111", "0111011", "0110111", "0001011",
)
_EAN13_G_CODES = (
    "0100111", "0110011", "0011011", "0100001", "0011101",
    "0111001", "0000101", "0010001", "0001001", "0010111",
)
_EAN13_R_CODES = (
    "1110010", "1100110", "1101100", "1000010", "1011100",
    "1001110", "1010000", "1000100", "1001000", "1110100",
)
_EAN13_PARITY = (
    "LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG",
    "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL",
)


def normalize_ean13(value: str) -> str:
    """Remove espaços/hífens e valida o formato de um EAN-13."""
    if not isinstance(value, str):
        raise ValueError("EAN-13 deve ser informado como texto.")

    normalized = value.replace(" ", "").replace("-", "")
    if len(normalized) != 13:
        raise ValueError("EAN-13 deve conter exatamente 13 dígitos.")
    if any(character < "0" or character > "9" for character in normalized):
        raise ValueError("EAN-13 deve conter apenas dígitos, espaços e hífens.")

    return normalized


def calculate_ean13_check_digit(base12: str) -> str:
    """Calcula o dígito verificador EAN-13 para uma base de 12 dígitos."""
    if not isinstance(base12, str) or len(base12) != 12:
        raise ValueError("A base do EAN-13 deve conter exatamente 12 dígitos.")
    if any(character < "0" or character > "9" for character in base12):
        raise ValueError("A base do EAN-13 deve conter apenas dígitos.")

    weighted_sum = sum(
        int(character) * (1 if index % 2 == 0 else 3)
        for index, character in enumerate(base12)
    )
    return str((10 - weighted_sum % 10) % 10)


def validate_ean13(value: str) -> str:
    """Retorna o EAN-13 normalizado quando o dígito verificador é válido."""
    normalized = normalize_ean13(value)
    expected_check_digit = calculate_ean13_check_digit(normalized[:12])
    if normalized[-1] != expected_check_digit:
        raise ValueError("Dígito verificador inválido para o EAN-13.")

    return normalized


def generate_ean13(used_codes: Iterable[str]) -> str:
    """Gera o primeiro EAN-13 interno disponível com prefixo 200."""
    normalized_used_codes = {normalize_ean13(code) for code in used_codes}

    for suffix in range(1_000_000_000):
        base12 = f"200{suffix:09d}"
        candidate = base12 + calculate_ean13_check_digit(base12)
        if candidate not in normalized_used_codes:
            return validate_ean13(candidate)

    raise RuntimeError("Não há códigos EAN-13 internos disponíveis.")


def render_ean13_svg(value: str, height: int = 48) -> str:
    """Renderiza um EAN-13 validado em SVG responsivo e acessível."""
    normalized = validate_ean13(value)
    if not isinstance(height, int) or height <= 0:
        raise ValueError("A altura do código de barras deve ser positiva.")

    first_digit = int(normalized[0])
    parity = _EAN13_PARITY[first_digit]
    modules = ["101"]
    for digit, encoding in zip(normalized[1:7], parity):
        digit_index = int(digit)
        modules.append(
            _EAN13_L_CODES[digit_index]
            if encoding == "L"
            else _EAN13_G_CODES[digit_index]
        )
    modules.extend(("01010", "".join(_EAN13_R_CODES[int(digit)] for digit in normalized[7:])))
    pattern = "".join(modules + ["101"])

    quiet_zone = 3
    svg_height = height + 18
    guard_positions = {0, 2, 45, 49, 92, 94}
    bars = []
    for index, module in enumerate(pattern):
        if module != "1":
            continue
        bar_height = height + 4 if index in guard_positions else height
        bars.append(
            f'<rect x="{index + quiet_zone}" y="0" width="1" '
            f'height="{bar_height}" />'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="Código de barras {normalized}" width="100%" '
        f'height="{svg_height}" viewBox="0 0 101 {svg_height}" '
        f'preserveAspectRatio="none" shape-rendering="crispEdges">'
        f'<title>Código de barras {normalized}</title>'
        f'<g fill="currentColor">{"".join(bars)}</g>'
        f'<text x="50.5" y="{height + 14}" text-anchor="middle" '
        f'font-family="Arial, sans-serif" font-size="8" '
        f'letter-spacing="0.5">{normalized}</text>'
        "</svg>"
    )
    return mark_safe(svg)
