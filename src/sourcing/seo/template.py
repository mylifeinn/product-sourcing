from __future__ import annotations

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path
from typing import Dict, Any


TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)


PRODUCT_TEMPLATE = """{% raw %}
<div class="product-description">
  <!-- Problem → Agitation → Solution -->
  <section class="pas-framework">
    <h2>{{ title }}</h2>
    <p><strong>Problem:</strong> 你是否正在为 {{ pain_points | join("、") }} 而烦恼？</p>
    <p><strong>Agitation:</strong> 传统方案要么笨重、要么无效、要么价格虚高，让你在 {{ niche }} 的选择上反复踩坑。</p>
    <p><strong>Solution:</strong> {{ title }} —— 专为解决 {{ pain_points[0] if pain_points else "核心痛点" }} 而设计，{{ usp_framework }}。</p>
  </section>

  <!-- Specs Table with Schema.org Product markup -->
  <section class="specs" itemscope itemtype="https://schema.org/Product">
    <h3>产品规格</h3>
    <table>
      <thead>
        <tr><th>参数</th><th>详情</th></tr>
      </thead>
      <tbody>
        <tr><td itemprop="name">产品名称</td><td>{{ title }}</td></tr>
        <tr><td>品类</td><td>{{ niche }}</td></tr>
        <tr><td>重量</td><td>{{ specs.weight_g }}g</td></tr>
        <tr><td>尺寸</td><td>{{ specs.dimensions_cm[0] }}×{{ specs.dimensions_cm[1] }}×{{ specs.dimensions_cm[2] }} cm</td></tr>
        <tr><td>材质</td><td>{{ specs.material }}</td></tr>
        <tr><td>品牌</td><td itemprop="brand">{{ brand_voice }}</td></tr>
      </tbody>
    </table>
    
    <meta itemprop="sku" content="{{ title | replace(' ', '-') | lower }}" />
    <meta itemprop="weight" content="{{ specs.weight_g }}" />
  </section>

  <!-- FAQ with FAQPage Schema -->
  <section class="faq" itemscope itemtype="https://schema.org/FAQPage">
    <h3>常见问题</h3>
    {% for q, a in faqs.items() %}
    <div itemscope itemprop="mainEntity" itemtype="https://schema.org/Question">
      <h4 itemprop="name">{{ q }}</h4>
      <div itemscope itemprop="acceptedAnswer" itemtype="https://schema.org/Answer">
        <p itemprop="text">{{ a }}</p>
      </div>
    </div>
    {% endfor %}
  </section>

  <!-- Trust Signals -->
  <section class="trust-signals">
    <h3>为什么选择我们</h3>
    <ul>
      <li>{{ trust_anchors }}</li>
      <li>30天无理由退换货</li>
      <li>全球免费配送（美国境内3-7天达）</li>
      <li>24/7 客服支持</li>
    </ul>
  </section>

  <!-- Internal Links Block -->
  <section class="related-products">
    <h3>你可能也喜欢</h3>
    <ul>
      <li><a href="/collections/{{ niche | lower | replace(' ', '-') }}">更多 {{ niche }} 产品</a></li>
      <li><a href="/blogs/guides/how-to-choose-{{ niche | lower | replace(' ', '-') }}">{{ niche }} 选购指南</a></li>
    </ul>
  </section>

  <!-- BreadcrumbList Schema -->
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
      {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://yourstore.com"},
      {"@type": "ListItem", "position": 2, "name": "{{ niche }}", "item": "https://yourstore.com/collections/{{ niche | lower | replace(' ', '-') }}"},
      {"@type": "ListItem", "position": 3, "name": "{{ title }}", "item": "https://yourstore.com/products/{{ title | replace(' ', '-') | lower }}"}
    ]
  }
  </script>
</div>
{% endraw %}"""


# Write template file
template_path = TEMPLATE_DIR / "product_description.html.j2"
template_path.write_text(PRODUCT_TEMPLATE, encoding="utf-8")


# Jinja2 environment
env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_product_description(
    title: str,
    niche: str,
    pain_point_keywords: list[str],
    specs: Dict[str, Any],
    brand_voice: str,
    usp_framework: str,
    trust_anchors: str,
) -> str:
    """Render product description HTML from template"""
    template = env.get_template("product_description.html.j2")
    
    # Generate FAQs from pain points
    faqs = {}
    for i, kw in enumerate(pain_point_keywords[:5]):
        faqs[f"这个 {niche} 真的能解决 {kw} 吗？"] = (
            f"是的，{{ title }} 专门针对 {kw} 设计，通过 {{ usp_framework }} 实现显著改善。"
        )
        faqs[f"发货需要多久？"] = "美国本土仓发货，通常 3-7 个工作日送达。"
        faqs[f"质量有保障吗？"] = f"{{ trust_anchors }}，且支持 30 天无理由退换。"
    
    return template.render(
        title=title,
        niche=niche,
        pain_points=pain_point_keywords,
        specs=specs,
        brand_voice=brand_voice,
        usp_framework=usp_framework,
        trust_anchors=trust_anchors,
        faqs=faqs,
    )


def render_seo_meta(
    title: str,
    niche: str,
    pain_point_keywords: list[str],
    brand_vendor: str,
    usp_framework: str,
) -> Dict[str, str]:
    """Generate SEO meta tags"""
    first_pain = pain_point_keywords[0] if pain_point_keywords else "核心痛点"
    return {
        "seo_title": f"{title} | {brand_vendor}",
        "seo_description": f"解决{first_pain}痛点的{niche}，{usp_framework}。免费配送，30天退换。",
        "geo_keywords": ",".join(pain_point_keywords + [niche]),
    }