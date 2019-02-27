from polling_regions import polling_regions
from polling_essentials import parties, regions, RegionalTotals, Poll, Seat
import csv, json, math, sys
from datetime import datetime

seats = {}
regional = {}
polls = {}

arguments = sys.argv

model_map = {
    "650" : ["2017election.json", "prediction.json"],
    "600" : ["2017election_600seat.json", "prediction_600seat.json"]
}

print model_map[arguments[1]]

poll_limit = arguments[2]

with open("../" + model_map[arguments[1]][0]) as f:
    data = json.load(f)
    for seat in data:
        seats[seat] = Seat(data[seat])

# get old vote numbers for each region
for region in regions:
    regional[region] = RegionalTotals(region)
    regional[region].old_totals = regional[region].get_regional_totals(seats)

# get polls from csv


polling_data = "polls.csv"

with open(polling_data, "rb") as polls_file:
    poll_data = csv.DictReader(polls_file, delimiter = "\t")
    poll_codes = []
    poll_rows = []

    for row in poll_data:
        
        code = row["code"]
        if code not in poll_codes:

            polls[code] = Poll(code, row["company"], row["day"], row["month"], row["year"])
            poll_codes.append(code)

        polls[code].add_row(row)

    polls_file.close()

polls_for_scatterplot = []
exclude_for_scatter = ["yougovscotland", "survationscotland",
                        "panelbasescotland", "yougovwales",
                        "lucidtalk", "yougovlondon"]
#configure, weight and log polls
for poll, data in polls.iteritems():
    # do first since delete total later on
    if data.company != "me" and data.company not in exclude_for_scatter:
        polls_for_scatterplot.append(data.scatterplot())

    # turn all to decimals and fix some companies data
    data.poll_maths()
    #weight poll based on time since
    data.weight = data.weight_poll()

# sort for display on site (moving average line)
polls_for_scatterplot.sort(key=lambda x: x["dateobj"])
for poll in polls_for_scatterplot:
    del poll["dateobj"]

#get change for each polling region relative to 2015
for poll, data in polls.iteritems():
    if poll != "1001":# my test poll nullified
        for area, numbers in data.regions.iteritems():
            regions_in_poll_area = polling_regions[data.company][area]
            for party in numbers:
                #get previous regional total per party
                previous_total = 0
                previous_turnout = 0
                for region in regions_in_poll_area:
                    previous_turnout += regional[region].old_totals[party]
                    previous_total += regional[region].old_totals["turnout"]

                previous_area_percentage = previous_turnout / float(previous_total)
                #alter poll to show change

                numbers[party] -= previous_area_percentage

            for region in regions_in_poll_area:
                for party in numbers:
                    if party not in regional[region].new_totals:
                        regional[region].new_totals[party] = 0

                    previous_region_percentage = regional[region].old_totals[party] / float(regional[region].old_totals["turnout"]) #2015 region percentage
                    regional[region].new_totals[party] += (data.weight * (previous_region_percentage + numbers[party])) # add poll region and weight it


#END POLL ANALYSIS

#START SEAT ANALYSIS
for region, data in regional.iteritems():

    data.normalise()
    data.get_old_percentages()
    data.get_relative_change()
    data.get_numerical_change()


to_dump = {}

for seat, info in seats.iteritems():

    info.get_new_data(regional[info.region].numerical)
    info.generate_output()
    to_dump[seat] = info.output


with open("../" + model_map[arguments[1]][1], "w") as output:
    json.dump(to_dump, output)


#below here non model stuff
#generate data for scatter
def dump_scatter_data():
    # for displaying model and polls on scatterplot
    last_date = datetime(2015, 5, 1)
    for code in polls:
        if polls[code].date > last_date:
            last_date = polls[code].date

    date_code = '{:%m/%d/%Y}'.format(last_date)

    seat_totals = {
        "date" : date_code,
        "labour" : 0,
        "conservative" : 0,
        "libdems" : 0,
        "snp" : 0,
        "ukip" : 0,
        "green" : 0,
        "others" : 0
    }

    for seat, data in seats.iteritems():
        winner = data.current
        if winner in seat_totals:
            seat_totals[winner] += 1
        else:
            seat_totals["others"] += 1

    with open("../../polltracker/scatter.json") as  scatter_input:
        scatter_data = json.load(scatter_input)

    duplicate_dates = []
    for i in range(len(scatter_data["models"])):
        #print scatter_data["models"][i]["date"]
        if scatter_data["models"][i]["date"] == date_code:
            duplicate_dates.append(i)

    for i in range(len(duplicate_dates), 0, -1):
        del scatter_data["models"][duplicate_dates[i-1]]

    scatter_data["models"].append(seat_totals)
    scatter_data["polls"] = polls_for_scatterplot

    with open("../../polltracker/scatter.json", "w") as scatter_json:
        json.dump(scatter_data, scatter_json)

    print poll_limit, last_date
    print seat_totals

if arguments[1] == "650":
    dump_scatter_data()

# dump last pollster
max = 0
for poll in polls:
    if int(poll) > max and poll not in ["1001", "1000"]:
        max = int(poll)

last = str(max)

last_updated = datetime.now().strftime("%I:%M%p on %B %d, %Y")
last_pollster = last_updated + " (" + polls[last].company + ")"

with open("../../lastpollster.html", "w") as pollster_html:
    pollster_html.write(last_pollster)
