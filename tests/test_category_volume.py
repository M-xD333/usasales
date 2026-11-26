import pytest
import bot


@pytest.mark.parametrize('title,expected',[
 ('Montech TG3 ATX Mid Tower, ARGB Fans, RTX 5090 Support','case'),
 ('HYTE Y70 Touch Infinite Mid Tower Aesthetic Gaming PC Case with 2.5K IPS Touchscreen','case'),
 ('GAMDIAS Micro‑Tower Gaming PC Case with Prebuilt Fans','case'),
 ('Montech AIR 903 MAX Ultra-Cooling Mid-Tower','case'),
 ('Thermaltake AX700 Snow TG Super Tower','case'),
 ('Fractal Design North with Mesh Side Panel Mid-Tower PC Case','case'),
 ('Intel Core i9-14900KF New Gaming Desktop Processor 24 cores','cpu'),
 ('Corsair Vengeance DDR5 SODIMM Laptop Memory','ram'),
 ('Newegg 1TB Portable SSD Combo for Laptop, Phone, Computer','ext_storage'),
 ('Corsair RS360 II ARGB Unified Frame PWM Fans','cooler'),
 ('Lian Li UNI FAN SL Infinity 140 Reverse Blade','cooler'),
 ('Corsair iCUE LINK LX360 RGB 360mm Unified Triple Fans','cooler'),
 ('Corsair iCUE RX120 PWM Computer Case Fan Starter Kit','cooler'),
 ('Ryzen 7 9800X3D with free PC case','cpu'),
 ('GeForce RTX 5090 Graphics Card compatible with mid tower cases','gpu'),
 ('Ryzen 9 Desktop Computer 32GB DDR5 2TB SSD RGB PWM Fans','prebuilt'),
 ('Laptop 16GB RAM 1TB SSD 15 inch Ryzen 7 with PWM Fans','laptop'),
 ('ASUS Motherboard compatible with ATX mid tower cases','mobo'),
 ('Enclosure for Portable SSD','ext_storage'),
])
def test_primary_component_is_not_compatibility_or_accessory(title,expected):
    assert bot.classify(title)==expected
