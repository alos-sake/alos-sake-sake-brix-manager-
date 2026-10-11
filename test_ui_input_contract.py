from pathlib import Path
import unittest

APP = Path(__file__).with_name("app.py").read_text(encoding="utf-8")


class SugarInputUIContractTests(unittest.TestCase):
    def test_direct_input_uses_mobile_decimal_keyboard(self):
        self.assertIn("input.inputMode='decimal'", APP)
        self.assertIn("input.placeholder='例：8.45'", APP)
        self.assertNotIn("st.number_input(", APP)

    def test_six_compact_rows_and_one_shared_wheel(self):
        self.assertIn("for(let i=0;i<6;i++)", APP)
        self.assertIn('component_key = f"brix_measurements_{reset_token}"', APP)
        self.assertIn("height=420", APP)
        self.assertIn("height:78px", APP)
        self.assertIn("const rowHeight = 28;", APP)

    def test_wheel_supports_touch_and_keyboard(self):
        self.assertIn("touch-action:pan-y", APP)
        self.assertIn("-webkit-overflow-scrolling:touch", APP)
        self.assertIn("ArrowUp", APP)
        self.assertIn("ArrowDown", APP)

    def test_wheel_updates_selected_value_and_enable_state(self):
        self.assertIn("input.value=value.toFixed(2)", APP)
        self.assertIn("checkEls[target].checked=true", APP)
        self.assertIn("setStateValue('value', next)", APP)

    def test_direct_values_are_validated_without_aggregates(self):
        self.assertIn('decimal_value.as_tuple().exponent < -2', APP)
        self.assertIn('小数第2位まで入力してください', APP)
        self.assertIn('raw[f"m{i}"] = None', APP)
        self.assertNotIn("statistics.mean", APP)

    def test_reset_clears_values_and_recreates_component(self):
        self.assertIn('"values": [""] * 6', APP)
        self.assertIn('wheel_reset_token', APP)


if __name__ == "__main__":
    unittest.main()
