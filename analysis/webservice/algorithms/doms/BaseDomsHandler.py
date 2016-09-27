from datetime import datetime
from pytz import timezone, UTC
from webservice.NexusHandler import NexusHandler as BaseHandler
from webservice.webmodel import StatsComputeOptions
from webservice.NexusHandler import nexus_handler
from webservice.NexusHandler import DEFAULT_PARAMETERS_SPEC
from webservice.webmodel import NexusResults, NexusProcessingException
import os
import json
import config
import numpy as np
import geo

EPOCH = timezone('UTC').localize(datetime(1970, 1, 1))

try:
    from osgeo import gdal
    from osgeo.gdalnumeric import *
except ImportError:
    import gdal
    from gdalnumeric import *
from netCDF4 import Dataset
from os import listdir
from os.path import isfile, join
import tempfile


class BaseDomsQueryHandler(BaseHandler):
    def __init__(self):
        BaseHandler.__init__(self)

    def getDataSourceByName(self, source):
        for s in config.ENDPOINTS:
            if s["name"] == source:
                return s
        return None

    def _does_datasource_exist(self, ds):
        for endpoint in config.ENDPOINTS:
            if endpoint["name"] == ds:
                return True
        return False


class DomsEncoder(json.JSONEncoder):
    def __init__(self, **args):
        json.JSONEncoder.__init__(self, **args)

    def default(self, obj):
        # print 'MyEncoder.default() called'
        # print type(obj)
        if obj == np.nan:
            return None  # hard code string for now
        elif isinstance(obj, datetime):
            return long((obj - EPOCH).total_seconds())
        else:
            return json.JSONEncoder.default(self, obj)


class DomsQueryResults(NexusResults):
    def __init__(self, results=None, args=None, bounds=None, count=None, details=None, computeOptions=None,
                 executionId=None, status_code=200):
        NexusResults.__init__(self, results=results, meta=None, stats=None, computeOptions=computeOptions,
                              status_code=status_code)
        self.__args = args
        self.__bounds = bounds
        self.__count = count
        self.__details = details
        self.__executionId = executionId

    def toJson(self):
        bounds = self.__bounds.toMap() if self.__bounds is not None else {}
        return json.dumps(
            {"executionId": self.__executionId, "data": self.results(), "params": self.__args, "bounds": bounds,
             "count": self.__count, "details": self.__details}, indent=4, cls=DomsEncoder)

    def toCSV(self):
        pass




    def toNetCDF(self):
        return DomsNetCDFFormatter.create(self.__executionId, self.results(), self.__args, self.__details)


class DomsNetCDFFormatter:

    @staticmethod
    def create(executionId, results, params, details):
        t = tempfile.mkstemp(prefix="doms_", suffix=".nc")
        tempFileName = t[1]

        dataset = Dataset(tempFileName, "w", format="NETCDF4")

        dataset.matchID = executionId
        dataset.Matchup_TimeWindow = params["timeTolerance"]
        dataset.Matchup_TimeWindow_Units = "hours"

        dataset.time_coverage_start = datetime.fromtimestamp(params["startTime"] / 1000).strftime('%Y%m%d %H:%M:%S')
        dataset.time_coverage_end = datetime.fromtimestamp(params["endTime"] / 1000).strftime('%Y%m%d %H:%M:%S')
        dataset.depth_tolerance = params["depthTolerance"]
        dataset.platforms = params["platforms"]

        dataset.Matchup_SearchRadius = params["radiusTolerance"]
        dataset.Matchup_SearchRadius_Units = "m"

        dataset.bounding_box = params["bbox"]
        dataset.primary = params["primary"]
        dataset.secondary = ",".join(params["matchup"])

        dataset.Matchup_ParameterPrimary = params["parameter"] if "parameter" in params else ""

        dataset.time_coverage_resolution = "point"

        bbox = geo.BoundingBox(asString=params["bbox"])
        dataset.geospatial_lat_max = bbox.north
        dataset.geospatial_lat_min = bbox.south
        dataset.geospatial_lon_max = bbox.east
        dataset.geospatial_lon_min = bbox.west
        dataset.geospatial_lat_resolution = "point"
        dataset.geospatial_lon_resolution = "point"
        dataset.geospatial_lat_units = "degrees_north"
        dataset.geospatial_lon_units = "degrees_east"
        dataset.geospatial_vertical_min = 0.0
        dataset.geospatial_vertical_max = params["radiusTolerance"]
        dataset.geospatial_vertical_units = "m"
        dataset.geospatial_vertical_resolution = "point"
        dataset.geospatial_vertical_positive = "down"

        dataset.time_to_complete = details["timeToComplete"]
        dataset.num_insitu_matched = details["numInSituMatched"]
        dataset.num_gridded_checked = details["numGriddedChecked"]
        dataset.num_gridded_matched = details["numGriddedMatched"]
        dataset.num_insitu_checked = details["numInSituChecked"]

        dataset.date_modified = datetime.now().strftime('%Y%m%d %H:%M:%S')
        dataset.date_created = datetime.now().strftime('%Y%m%d %H:%M:%S')

        DomsNetCDFFormatter.__addNetCDFConstants(dataset)

        idList = []
        primaryIdList = []
        DomsNetCDFFormatter.__packDataIntoDimensions(idList, primaryIdList, results)

        idDim = dataset.createDimension("id", size=None)
        primaryIdDim = dataset.createDimension("primary_id", size=None)

        idVar = dataset.createVariable("id", "i4", ("id",))
        primaryIdVar = dataset.createVariable("primary_id", "i4", ("primary_id",))

        idVar[:] = idList
        primaryIdVar[:] = primaryIdList

        DomsNetCDFFormatter.__createDimension(dataset, results, "lat", "f4", "y")
        DomsNetCDFFormatter.__createDimension(dataset, results, "lon", "f4", "x")

        DomsNetCDFFormatter.__createDimension(dataset, results, "sea_water_temperature_depth", "f4", "sea_water_temperature_depth")
        DomsNetCDFFormatter.__createDimension(dataset, results, "sea_water_temperature", "f4", "sea_water_temperature")
        DomsNetCDFFormatter.__createDimension(dataset, results, "sea_water_salinity_depth", "f4", "sea_water_salinity_depth")
        DomsNetCDFFormatter.__createDimension(dataset, results, "sea_water_salinity", "f4", "sea_water_salinity")

        DomsNetCDFFormatter.__createDimension(dataset, results, "wind_speed", "f4", "wind_speed")
        DomsNetCDFFormatter.__createDimension(dataset, results, "wind_direction", "f4", "wind_direction")
        DomsNetCDFFormatter.__createDimension(dataset, results, "wind_u", "f4", "wind_u")
        DomsNetCDFFormatter.__createDimension(dataset, results, "wind_v", "f4", "wind_v")

        DomsNetCDFFormatter.__createDimension(dataset, results, "time", "f4", "time")
        dataset.close()

        f = open(tempFileName, "rb")
        data = f.read()
        f.close()
        os.unlink(tempFileName)
        return data

    @staticmethod
    def __packDataIntoDimensions(idVar, primaryIdVar, values, primaryValueId=None):

        idIndex = primaryValueId + 1 if primaryValueId is not None else 0

        for value in values:
            idVar.append(idIndex)
            primaryIdVar.append(primaryValueId if primaryValueId is not None else -1)
            idIndex = idIndex + 1

            if "matches" in value and len(value["matches"]) > 0:
                idIndex = DomsNetCDFFormatter.__packDataIntoDimensions(idVar, primaryIdVar, value["matches"], idIndex)

        return idIndex

    @staticmethod
    def __packDimensionList(values, field, varList):
        for value in values:
            if field in value:
                varList.append(value[field])
            else:
                varList.append(np.nan)
            if "matches" in value and len(value["matches"]) > 0:
                DomsNetCDFFormatter.__packDimensionList(value["matches"], field, varList)

    @staticmethod
    def __createDimension(dataset, values, name, type, arrayField):
        dim = dataset.createDimension(name, size=None)
        var = dataset.createVariable(name, type, (name,))

        varList = []
        DomsNetCDFFormatter.__packDimensionList(values, arrayField, varList)

        var[:] = varList

    @staticmethod
    def __addNetCDFConstants(dataset):
        dataset.bnds = 2
        dataset.Conventions = "CF-1.6, ACDD-1.3"
        dataset.title = "DOMS satellite-insitu machup output file"
        dataset.history = "Processing_Version = V1.0, Software_Name = DOMS, Software_Version = 1.03"
        dataset.institution = "JPL, FSU, NCAR"
        dataset.source = "doms.jpl.nasa.gov"
        dataset.standard_name_vocabulary = "CF Standard Name Table v27", "BODC controlled vocabulary"
        dataset.cdm_data_type = "Point/Profile, Swath/Grid"
        dataset.processing_level = "4"
        dataset.platform = "Endeavor"
        dataset.instrument = "Endeavor on-board sea-bird SBE 9/11 CTD"
        dataset.project = "Distributed Oceanographic Matchup System (DOMS)"
        dataset.keywords_vocabulary = "NASA Global Change Master Directory (GCMD) Science Keywords"
        dataset.keywords = "Salinity, Upper Ocean, SPURS, CTD, Endeavor, Atlantic Ocean"
        dataset.creator_name = "NASA PO.DAAC"
        dataset.creator_email = "podaac@podaac.jpl.nasa.gov"
        dataset.creator_url = "https://podaac.jpl.nasa.gov/"
        dataset.publisher_name = "NASA PO.DAAC"
        dataset.publisher_email = "podaac@podaac.jpl.nasa.gov"
        dataset.publisher_url = "https://podaac.jpl.nasa.gov"
        dataset.acknowledgment = "DOMS is a NASA/AIST-funded project.  Grant number ####."