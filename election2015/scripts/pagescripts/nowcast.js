// gets vote totals for each region and adds data to relevant array for use when user elects
function getVoteTotals(data, region) {

		region = region.slice(34);
		$.each(data, function(i){
			var info = {};
			info["code"] = data[i].code;
			info["seats"] = data[i].seats;
			info["change"] = data[i].change;
			info["votepercent"] = data[i].votepercent;
			info["votepercentchange"] = data[i].votepercentchange;


		if (region == "greatbritain.csv")
			greatbritainVoteTotals.push(info)

		if (region == "england.csv")
			englandVoteTotals.push(info)

		if (region == "scotland.csv")
			scotlandVoteTotals.push(info)

		if (region == "wales.csv")
			walesVoteTotals.push(info)

		if (region == "northernireland.csv")
			northernirelandVoteTotals.push(info)

		if (region == "eastofengland.csv")
			eastofenglandVoteTotals.push(info)

		if (region == "northeastengland.csv")
			northeastenglandVoteTotals.push(info)

		if (region == "northwestengland.csv")
			northwestenglandVoteTotals.push(info)

		if (region == "southeastengland.csv")
			southeastenglandVoteTotals.push(info)

		if (region == "southwestengland.csv")
			southwestenglandVoteTotals.push(info)

		if (region == "london.csv")
			londonVoteTotals.push(info)

		if (region == "eastmidlands.csv")
			eastmidlandsVoteTotals.push(info)

		if (region == "westmidlands.csv")
			westmidlandsVoteTotals.push(info)

		if (region == "yorkshireandthehumber.csv")
			yorkshireandthehumberVoteTotals.push(info)

		});
}

// get relevant data for map
function getMapInfo(){
	parseData("/election2015/data/nowcast.csv", getSeatInfo);
}


// get vote totals for initial display and possible future display
function getInfoFromFiles(){

	parseData("/election2015/data/nowcastregions/nowcastvotetotals.csv", getVoteTotalsInitial);

	// get rest of vote totals for later

	parseData("/election2015/data/nowcastregions/greatbritain.csv", getVoteTotals);
	parseData("/election2015/data/nowcastregions/england.csv", getVoteTotals);
	parseData("/election2015/data/nowcastregions/scotland.csv", getVoteTotals);
	parseData("/election2015/data/nowcastregions/wales.csv", getVoteTotals);
	parseData("/election2015/data/nowcastregions/northernireland.csv", getVoteTotals);
	parseData("/election2015/data/nowcastregions/northeastengland.csv", getVoteTotals);
	parseData("/election2015/data/nowcastregions/northwestengland.csv", getVoteTotals);
	parseData("/election2015/data/nowcastregions/southeastengland.csv", getVoteTotals);
	parseData("/election2015/data/nowcastregions/southwestengland.csv", getVoteTotals);
	parseData("/election2015/data/nowcastregions/london.csv", getVoteTotals);
	parseData("/election2015/data/nowcastregions/eastmidlands.csv", getVoteTotals);
	parseData("/election2015/data/nowcastregions/westmidlands.csv", getVoteTotals);
	parseData("/election2015/data/nowcastregions/eastofengland.csv", getVoteTotals);
	parseData("/election2015/data/nowcastregions/yorkshireandthehumber.csv", getVoteTotals);

};

// initiate flow
$(document).ready(function(){ getMapInfo()});
$(document).ready(function(){ getInfoFromFiles()});
