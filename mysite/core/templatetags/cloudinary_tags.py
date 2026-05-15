import os
from django import template
from django.conf import settings
import cloudinary

register = template.Library()

# Mapping from relative path to Cloudinary public_id
# This maps Django static paths to Cloudinary public_ids (with ring0 prefix)
CLOUDINARY_PATH_MAP = {
    'ringui/b1.png': 'ring0/static/ringui/B1',
    'ringui/b2.png': 'ring0/static/ringui/B2',
    'ringui/b3.png': 'ring0/static/ringui/B3_compressed',
    'ringui/b4.png': 'ring0/static/ringui/B4',
    'ringui/b5.png': 'ring0/static/ringui/B5',
    'ringui/b6.png': 'ring0/static/ringui/B6',
    'ringui/d1.png': 'ring0/static/ringui/D1',
    'ringui/d2.png': 'ring0/static/ringui/D2',
    'ringui/d3.png': 'ring0/static/ringui/D3',
    'ringui/d4.png': 'ring0/static/ringui/D4',
    'ringui/d5.png': 'ring0/static/ringui/D5',
    'ringui/d6.png': 'ring0/static/ringui/D6',
    'company/ceo.jpg': 'ring0/static/company/CEO',
    'company/cot.png': 'ring0/static/company/cot',
    'company/uni.png': 'ring0/static/company/uni',
    'company/uni-cot.png': 'ring0/static/company/UNI-COT',
    'company/ring0.png': 'ring0/static/company/ring0',
    'company/dfix.png': 'ring0/static/company/DFIX',
    'company/dcx.png': 'ring0/static/company/dcx',
    'company/hotb.png': 'ring0/static/company/hotb',
    'company/mc.png': 'ring0/static/company/mc',
    'ringui/company/ceo.jpg': 'ring0/static/company/CEO',
    'ringui/company/cot.png': 'ring0/static/company/cot',
    'ringui/company/uni.png': 'ring0/static/company/uni',
    'ringui/company/uni-cot.png': 'ring0/static/company/UNI-COT',
    'ringui/company/ring0.png': 'ring0/static/company/ring0',
    'ringui/company/dfix.png': 'ring0/static/company/DFIX',
    'ringui/company/dcx.jpg': 'ring0/static/company/dcx',
    'ringui/company/dcx.png': 'ring0/static/company/dcx',
    'ringui/company/hotb.png': 'ring0/static/company/hotb',
    'ringui/company/mc.png': 'ring0/static/company/mc',
    'service/images/ringui/b1.png': 'ring0/service/ringui/B1',
    'service/images/ringui/b2.png': 'ring0/service/ringui/B2',
    'service/images/ringui/b3.png': 'ring0/service/ringui/B3_compressed',
    'service/images/ringui/b4.png': 'ring0/service/ringui/B4',
    'service/images/ringui/b5.png': 'ring0/service/ringui/B5',
    'service/images/ringui/b6.png': 'ring0/service/ringui/B6',
    'service/images/ringui/d1.png': 'ring0/service/ringui/D1',
    'service/images/ringui/d2.png': 'ring0/service/ringui/D2',
    'service/images/ringui/d3.png': 'ring0/service/ringui/D3',
    'service/images/ringui/d4.png': 'ring0/service/ringui/D4',
    'service/images/ringui/d5.png': 'ring0/service/ringui/D5',
    'service/images/ringui/d6.png': 'ring0/service/ringui/D6',
    'service/images/ringui/company/ceo.jpg': 'ring0/service/ringui/CEO',
    'service/images/ringui/company/cot.png': 'ring0/service/ringui/cot',
    'service/images/ringui/company/uni.png': 'ring0/service/ringui/uni',
    'service/images/ringui/company/uni-cot.png': 'ring0/service/ringui/UNI-COT',
    'service/images/ringui/company/ring0.png': 'ring0/service/ringui/ring0',
}


def get_cloudinary_url(relative_path):
    """Convert a relative path to a Cloudinary URL"""
    # Normalize the path
    path = relative_path.lower().lstrip('/')
    
    # Remove static prefix if present
    if path.startswith('static/'):
        path = path[7:]
    
    # Check mapping
    public_id = CLOUDINARY_PATH_MAP.get(path)
    
    if public_id:
        return cloudinary.CloudinaryImage(public_id).build_url(secure=True)
    
    # Fallback: try cloudinary directly with the path
    return cloudinary.CloudinaryImage(f'ring0/static/{path}').build_url(secure=True)


@register.simple_tag
def cloudinary_static(relative_path):
    """Template tag to get a Cloudinary URL for a static file"""
    return get_cloudinary_url(relative_path)
