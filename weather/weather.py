#SERVER NOTES
#MCP acts as a wrapper around different functionalities. Here, the MCP is acting as a wrapper around the NWS API. 
#What are some different things MCP can do?
##Tools: Functions/actions the server can perform, and they have names/descriptions/parameters which make them usable by an LLM. In this server, get_alerts and get_forecast are the tools
##Resources: File-like data clients can read, like API responses, file contents, logs
##Prompts: Templates that help users accomplish tasks. Basically a guide for LLMs on HOW to use these tools/resources above

#CLIENT NOTES
#Component (in this case Claude for Desktop) which knows how to speak MCP protocol, intermediary btwn LLM and MCP servers
#AI model tells MCP what tool/resource/parameters -> MCP client sends request to server -> gets responses, passes back to AI model
##IMPORTANT: LLM never directly calls ext APIs or services, tells MCP client, MCP client handles execution through server
###What does MCP client handle? Lifestyle w servers (init, msge exchange, termination), protocol neg (what features supported), and security boundaries

#HOST NOTES
#ai application user interacts with (claude desktop). In our case, it'll be the web app/or application within excel
#Contains/manages 1+ MCP clients
#Interprets user requests, decides with LLM help which MCP server capability is needed, and tells MCP client to make the call

#COMM FLOW: User input -> host & llm processing -> client request -> server execution -> server response -> client to host/llm -> llm makes response -> user display to user

from typing import Any # Lets you make a var or function of any type
import httpx # Third-party python library which makes HTTP reqs
from mcp.server.fastmcp import FastMCP # mcp is a package from the MCP SDK, gets the server subpackage, and imports FastMap class from the  SDK 
# define FastMCP server
mcp = FastMCP("weather") #passing name of mcp server (so client called claude will use "weather" as key to launch the server)
# constants (all caps)
NWS_API_BASE = "https://api.weather.gov" # this is the base url for the nws api, which will let us append different apths to get full api endpoint urls. For Sage, switch api to rest/excel one?
USER_AGENT = "weather-app/1.0" # header which identifies the client making the server request, lets api providers know what applctns accessing services/enforce rate limits
# note that requests coming from server are part of version 1.0 of the weather application

# Helper function for getting/formatting National Weather Service API data
async def make_nws_request(url: str) -> dict[str, Any] | None:
    #""""function is expected to return a dictionary (keys are string, values are of any type) OR None - common for JSON responses"""
    # Making headers dictionary of HTTPs
    headers = {
        "User-Agent": USER_AGENT, #user agent header, identifies application
        "Accept": "application/geo+json" #Tells server what content types our client/application will accept (prefer GeoJSON format)  //which format will we accept from excel app?
    }
    async with httpx.AsyncClient() as client: #making client instance instead of httpx.get, better practice for multiple requests. (NOTE: check out 'asynch with') prob good practice for this too
        try:
            response = await client.get(url, headers = headers, timeout=30.0) #pause execution of current function until client.get() is complete 
            #Note: other tasks can be run in the meantime, keeping server responsive
            #Tells httpx to send an HTTP GET request to this url
            #Passes in headers dictionary to include user-agent and accept preferences
            #Times out if nws api takes more than 30s
            response.raise_for_status() #if there's an error, this will raise an httpstatuserror exception
            return response.json() #if above was successful, executes!
        except Exception: #broad exception catch, for the actual implementation may be good to catch specific exceptions and handle them differently for debugging  (research diff exceptions to throw based on our application)
            return None #If there's any exception within the try block, jump execution here. the function returns none, so request didn't get valid date
            # None return value is checked by calling functions (get_alerts, get_forecast to see if they got data or need to return an error message)

#https://www.weather.gov/documentation/services-web-api#/default/alerts_query - Here's the link to the API, schema has the features being used here!
##application/geo+json - that's how they knew to do geo+json content under headers
##NOTED: compare Sage API with this to figure out where the formatting is similar/different

#NWS API alert requests return data in GeoJSON format, good for computers but not human readable. format_alert converts NWS API object (raw, machine readable) and converts it into something that is
#concise and human readable, which (that string) will be fed into the LLM to understand the alert deets without going throgh complicated JSON
def format_alert(feature: dict) -> str: #feature should be a dictionary since weather alerts are a list of features, each feature is a dictionary representing that alert.
    props = feature["properties"] #access properties key with dictionary on all weather alert details, extracts dictionary so we don't need to do something like feature["properties"]["event"]
    return f"""
Event: {props.get('event', 'Unknown')} 
Area: {props.get('areaDesc', 'Unknown')}
Severity: {props.get('severity', 'Unknown')}
Description: {props.get('description', 'No description available')}
Instructions: {props.get('instruction', 'No specific instructions provided')}
"""
#event is static text, by getting 'event', 'Unknown,' we make sure that if event key is missing, instead of raising a keyerror, it returns 'unknown' default value
#for my own feature, experiment with adding some different ones above^
#Is there a way to see where the API has missing/inconsistent information or go into the schema? Research later - maybe some sql stuff could be useful in case one of the features/parameters
#have a lot of missing data - potential optimization?





# #CODED
# def format_alert(feature: dict) -> str:
#     """Format an alert feature into a readable string."""
#     props = feature["properties"]
#     return f"""
# Event: {props.get('event', 'Unknown')}
# Area: {props.get('areaDesc', 'Unknown')}
# Severity: {props.get('severity', 'Unknown')}
# Description: {props.get('description', 'No description available')}
# Instructions: {props.get('instruction', 'No specific instructions provided')}
# """

@mcp.tool()
async def get_alerts(state: str) -> str:
    """Get weather alerts for a US state.

    Args:
        state: Two-letter US state code (e.g. CA, NY)
    """
    url = f"{NWS_API_BASE}/alerts/active/area/{state}"
    data = await make_nws_request(url)

    if not data or "features" not in data:
        return "Unable to fetch alerts or no alerts found."

    if not data["features"]:
        return "No active alerts for this state."

    alerts = [format_alert(feature) for feature in data["features"]]
    return "\n---\n".join(alerts)

@mcp.tool()
async def get_forecast(latitude: float, longitude: float) -> str:
    """Get weather forecast for a location.

    Args:
        latitude: Latitude of the location
        longitude: Longitude of the location
    """
    # First get the forecast grid endpoint
    points_url = f"{NWS_API_BASE}/points/{latitude},{longitude}"
    points_data = await make_nws_request(points_url)

    if not points_data:
        return "Unable to fetch forecast data for this location."

    # Get the forecast URL from the points response
    forecast_url = points_data["properties"]["forecast"]
    forecast_data = await make_nws_request(forecast_url)

    if not forecast_data:
        return "Unable to fetch detailed forecast."

    # Format the periods into a readable forecast
    periods = forecast_data["properties"]["periods"]
    forecasts = []
    for period in periods[:5]:  # Only show next 5 periods
        forecast = f"""
{period['name']}:
Temperature: {period['temperature']}°{period['temperatureUnit']}
Wind: {period['windSpeed']} {period['windDirection']}
Forecast: {period['detailedForecast']}
"""
        forecasts.append(forecast)

    return "\n---\n".join(forecasts)

if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')