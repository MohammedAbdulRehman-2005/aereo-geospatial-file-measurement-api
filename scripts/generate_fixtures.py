"""Script to generate all required test fixtures using pure-Python libraries."""
import os
import zipfile
import shapefile

fixtures_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures"))
sample_data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sample_data"))
os.makedirs(fixtures_dir, exist_ok=True)
os.makedirs(sample_data_dir, exist_ok=True)

# 1. road_line_4326.kml
road_kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Road Line Test</name>
    <Placemark>
      <name>Main Road</name>
      <description>Primary arterial road</description>
      <LineString>
        <coordinates>
          77.5946,12.9716,0
          77.6046,12.9716,0
          77.6046,12.9816,0
        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>"""
with open(os.path.join(fixtures_dir, "road_line_4326.kml"), "w", encoding="utf-8") as f:
    f.write(road_kml)

# 2. points_only.kml
points_kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Points Only Test</name>
    <Placemark>
      <name>Waypoint 1</name>
      <Point>
        <coordinates>77.5946,12.9716,0</coordinates>
      </Point>
    </Placemark>
    <Placemark>
      <name>Waypoint 2</name>
      <Point>
        <coordinates>77.6046,12.9816,0</coordinates>
      </Point>
    </Placemark>
  </Document>
</kml>"""
with open(os.path.join(fixtures_dir, "points_only.kml"), "w", encoding="utf-8") as f:
    f.write(points_kml)

# 3. mixed_geometry.kml
mixed_kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Mixed Geometry Test</name>
    <Placemark>
      <name>Parcel A</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              77.5,12.9,0
              77.51,12.9,0
              77.51,12.91,0
              77.5,12.91,0
              77.5,12.9,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
    <Placemark>
      <name>Access Road</name>
      <LineString>
        <coordinates>
          77.5,12.9,0
          77.52,12.9,0
        </coordinates>
      </LineString>
    </Placemark>
    <Placemark>
      <name>Survey Marker</name>
      <Point>
        <coordinates>77.505,12.905,0</coordinates>
      </Point>
    </Placemark>
  </Document>
</kml>"""
with open(os.path.join(fixtures_dir, "mixed_geometry.kml"), "w", encoding="utf-8") as f:
    f.write(mixed_kml)

# 4. invalid_geometry.kml (Bowtie self-intersecting polygon)
invalid_kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Invalid Geometry Test</name>
    <Placemark>
      <name>Bowtie Self Intersecting Polygon</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              77.5,12.9,0
              77.6,13.0,0
              77.6,12.9,0
              77.5,13.0,0
              77.5,12.9,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>"""
with open(os.path.join(fixtures_dir, "invalid_geometry.kml"), "w", encoding="utf-8") as f:
    f.write(invalid_kml)

# 5. sample_roads.zip (Shapefile)
temp_shp_dir = os.path.join(fixtures_dir, "_temp_shp")
os.makedirs(temp_shp_dir, exist_ok=True)
shp_base = os.path.join(temp_shp_dir, "sample_roads")

w = shapefile.Writer(shp_base, shapeType=shapefile.POLYLINE)
w.field("name", "C", size=50)
w.field("highway", "C", size=50)

w.line([[[77.5, 12.9], [77.6, 12.9], [77.7, 13.0]]])
w.record("Route 66", "primary")

w.line([[[77.5, 12.8], [77.5, 12.9]]])
w.record("Highway 1", "secondary")

w.close()

# Write WGS84 .prj file
wgs84_prj = 'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["Degree",0.017453292519943295]]'
with open(shp_base + ".prj", "w", encoding="utf-8") as f:
    f.write(wgs84_prj)

# Create sample_roads.zip
zip_path = os.path.join(fixtures_dir, "sample_roads.zip")
with zipfile.ZipFile(zip_path, "w") as zf:
    for ext in [".shp", ".shx", ".dbf", ".prj"]:
        file_to_pack = shp_base + ext
        if os.path.exists(file_to_pack):
            zf.write(file_to_pack, arcname="sample_roads" + ext)

# Also copy sample_roads.zip and example KML into sample_data
with open(os.path.join(sample_data_dir, "sample_roads.zip"), "wb") as f_out, open(zip_path, "rb") as f_in:
    f_out.write(f_in.read())
with open(os.path.join(sample_data_dir, "example.kml"), "w", encoding="utf-8") as f_out:
    f_out.write(road_kml)

# 6. missing_component.zip (omit .shx)
missing_zip = os.path.join(fixtures_dir, "missing_component.zip")
with zipfile.ZipFile(missing_zip, "w") as zf:
    for ext in [".shp", ".dbf", ".prj"]:
        file_to_pack = shp_base + ext
        if os.path.exists(file_to_pack):
            zf.write(file_to_pack, arcname="sample_roads" + ext)

# Clean up temp shapefile files
for ext in [".shp", ".shx", ".dbf", ".prj"]:
    p = shp_base + ext
    if os.path.exists(p):
        os.remove(p)
os.rmdir(temp_shp_dir)

# 7. malicious_paths.zip (Zip slip attempt)
malicious_zip = os.path.join(fixtures_dir, "malicious_paths.zip")
with zipfile.ZipFile(malicious_zip, "w") as zf:
    zf.writestr("../../etc/passwd", "root:x:0:0::/root:/bin/bash")
    zf.writestr("roads.shp", "dummy content")

print("All fixtures generated successfully!")
