from typing import Any # Lets you make a var or function of any type
import httpx # Third-party python library which makes HTTP reqs
from mcp.server.fastmcp import fastmcp # mcp is a package from the MCP SDK, gets the server subpackage, and imports FastMap class from the  SDK 

# define FastMCP server
mcp = fastmcp("weather") #passing name of mcp server (so client called claude will use "weather" as key to launch the server)

# constants (all caps)
NWS_API_BASE = "https://api.weather.gov" # this is the base url for the nws api, which will let us append different apths to get full api endpoint urls
USER_AGENT = "weather-app/1.0" # header which identifies the client making the server request, lets api providers know what applctns accessing services/enforce rate limits
                               # note that requests coming from server are part of version 1.0 of the weather application


# Helper function for getting/formatting National Weather Service API data
async def requestNWSAPI(url: str) -> dict[str, Any] | None:
    """"function is expected to return a dictionary (keys are string, values are of any type) OR None - common for JSON responses"""
    # Making headers dictionary of HTTPs
    headersDict = {
        "User-Agent": USER_AGENT, #user agent header, identifies application
        "Accept": "application/geo+json" #Tells server what content types our client/application will accept (prefer GeoJSON format)
    }
    async with httpx.AsyncClient() as client: #making client instance instead of httpx.get, better practice for multiple requests. (NOTE: check out 'asynch with')
        try:
            response = await client.get(url, headers = headersDict, timeout=30.0) #pause execution of current function until client.get() is complete 
            #Note: other tasks can be run in the meantime, keeping server responsive
            #Tells httpx to send an HTTP GET request to this url
            #Passes in headers dictionary to include user-agent and accept preferences
            #Times out if nws api takes more than 30s
            response.raise_for_status() #if there's an error, this will raise an httpstatuserror exception
            return response.json() #if above was successful, executes!
        except Exception: #broad exception catch, for the actual implementation may be good to catch specific exceptions and handle them differently for debugging
            return None #If there's any exception within the try block, jump execution here. the function returns none, so request didn't get valid date
            # None return value is checked by calling functions (get_alerts, get_forecast to see if they got data or need to return an error message)


if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')
