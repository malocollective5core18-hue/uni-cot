"""Context processor to provide Cloudinary URLs in templates"""
import cloudinary
from cloudinary import CloudinaryImage


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
    'ringui/company/ceo.jpg': 'ring0/static/company/CEO',
    'ringui/company/cot.png': 'ring0/static/company/cot',
    'ringui/company/uni.png': 'ring0/static/company/uni',
    'ringui/company/uni-cot.png': 'ring0/static/company/UNI-COT',
    'ringui/company/ring0.png': 'ring0/static/company/ring0',
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
}


def cloudinary_url(path):
    """Get a Cloudinary URL for a static path"""
    original = path.lower().lstrip('/')
    path = original.replace('.png', '').replace('.jpg', '')
    
    public_id = CLOUDINARY_PATH_MAP.get(path)
    if public_id:
        return cloudinary.CloudinaryImage(public_id).build_url(secure=True)
    # Also try with extension removed from original
    public_id = CLOUDINARY_PATH_MAP.get(original)
    if public_id:
        return cloudinary.CloudinaryImage(public_id).build_url(secure=True)
    return None


def cloudinary_urls(request):
    """Context processor to provide URL helper in templates"""
    return {
        'cloudinary_url': cloudinary_url,
    }
