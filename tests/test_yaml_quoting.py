import unittest
import pathlib
import yaml
from hydrator.krm import K8sResourceParser

class TestYamlQuoting(unittest.IsolatedAsyncioTestCase):
    async def test_leading_zero_quoting(self):
        parser = K8sResourceParser()
        yaml_input = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: test-config
data:
  store_id_octal: "012345"
  store_id_leading_zero: "09876"
  store_id_normal: "12345"
  store_id_zeros: "000"
"""
        path = pathlib.Path("test.yaml")
        unique_id = "test-cluster"
        
        # This will load and then dump the YAML
        output = await parser.process_yaml_string(yaml_input, path=path, unique_id=unique_id)
        
        self.assertTrue("'09876'" in output or '"09876"' in output, 
                        "09876 should be quoted in the output")
        
        self.assertTrue("'012345'" in output or '"012345"' in output, 
                        "012345 should be quoted in the output")
        
        self.assertTrue("'000'" in output or '"000"' in output, 
                        "000 should be quoted in the output")
        
        self.assertTrue("'12345'" in output or '"12345"' in output, 
                        "12345 should be quoted in the output")

        # Load it back to make sure it's still a string and has the same value
        loaded = list(yaml.safe_load_all(output))
        self.assertEqual(loaded[0]['data']['store_id_leading_zero'], "09876")
        self.assertEqual(loaded[0]['data']['store_id_octal'], "012345")
        self.assertEqual(loaded[0]['data']['store_id_normal'], "12345")
        self.assertEqual(loaded[0]['data']['store_id_zeros'], "000")
        
        self.assertIsInstance(loaded[0]['data']['store_id_leading_zero'], str)
        self.assertIsInstance(loaded[0]['data']['store_id_octal'], str)
        self.assertIsInstance(loaded[0]['data']['store_id_normal'], str)
        self.assertIsInstance(loaded[0]['data']['store_id_zeros'], str)

if __name__ == '__main__':
    unittest.main()
