import pytest
import product_weight as W
import direct_stores as D
from test_direct_stores import schema,URL


@pytest.mark.parametrize('marker',[{'hasVariant':[{'sku':'other'}]},{'isVariantOf':{'sku':'parent'}},{'@type':'ProductGroup'},{'hasVariant':[]},{'@type':['Product','ProductGroup']}])
def test_parent_or_ambiguous_variant_weight_never_enters_catalog_metadata(marker):
    product={**schema(),'weight':{'value':1,'unitCode':'KGM'},**marker}
    fields=W.from_product_fields(product,URL)
    assert fields=={'weight_missing_reason':'ambiguous_variants'}
    assert W.from_product(product) is None
    meta=D.offer_meta(product,product.get('offers',{}),URL)
    assert not meta.get('weight_pkg_kg') and not meta.get('weight_net_kg') and meta.get('weight_kg') is None
