// for live

/////ideas

// NOT DONE
// alter refresh delay rate based on time - for night itself - 11-12: 90s, 12-2 : 60 seconds, 2-5: 30s, 5-7: 60s, 7-: 120 seconds
// make css less shit


//DONE

//for refreshing, redo functions getseatinfo + loadmap + displayVoteTotals(nationalVoteTotals) every minute or so
// initiate while loop when user on site - delay of 60 seconds

// live ticker where user inputs was
// smilar while loop + refresh
// lists seats in order of declaration time
// scrollable + clickable
// possible coalitions


//////////// CHANGE**///////////x
// any reference to seatData[seatname] has changed
// seatData[seatname]["seat_info"] contains information about the seat in general (turnout/electorate/declaration time etc)
// seatData[seatname]["party_info"] contains names and vote totals for each party


//add to d3js prototype
// brings elements to top when (for use when clicking on seat)
d3.selection.prototype.moveToFront = function() {
							return this.each(function(){
							this.parentNode.appendChild(this);
						});
					};


var pageRefreshTotal = 0
// current state of user input filters
var filterStates = [{party: "null"}, {gain:"null"}, {region: "null"}, {majoritylow : 0}, {majorityhigh : 100}]

// empty arrays for various data
var seatsAfterFilter = []; // for use with user inputs in filters - changing map opacity + generating seat list at end
var searchSeatData = []; // for use with search box
var seatNames = []; // for use with search box
var filterToTicker = [];
var seatsToIDs = {}; // for mapping seats to ids to stop errors when seats arent populated with data
var currentSeats = []; // for use flashing new se

// control flow for analysing user filter inputs
function filterMap(setting){

	var	party = filterStates[0].party;
	var gains = filterStates[1].gain;
	var region = filterStates[2].region;
	var majoritylow = filterStates[3].majoritylow
	var majorityhigh = filterStates[4].majorityhigh

	// change nonsense user inputs
	if (isNaN(majoritylow))
		majoritylow = 0
	if (isNaN(majorityhigh))
		majorityhigh = 100

	// reset seatsAfterFilter from any previous filters.
	seatsAfterFilter = [];

	// use d3 to access seat list to cross reference seatData for filters - product of old way of doing it
	d3.json("/election2015/data/projection.json", function(uk){

			if (setting == "reset"){
				g.selectAll(".faded_not_here")
					.attr("class", "not_here")
				}
			else {
				g.selectAll(".not_here")
					.attr("class", "faded_not_here")
				}



			g.selectAll(".map")
				.attr("id", "filtertime")


			if (party == "null")
				g.selectAll("#filtertime")
					.attr("id", "partyfiltered")
			else
				g.selectAll("#filtertime")
					.attr("style", function(d){
						if (party != seatData[d.properties.name]["seat_info"]["winning_party"])
							return "opacity: 0.1";
						})
					.attr("id", function(d){
						if (party == seatData[d.properties.name]["seat_info"]["winning_party"])
							return "partyfiltered"
						});

			if (gains == "null")
					g.selectAll("#partyfiltered")
						.attr("id", "gainfiltered")

			else
				if (gains == "gains")
					g.selectAll("#partyfiltered")
						.attr("style", function(d) {
							if (seatData[d.properties.name]["seat_info"]["change"] == "hold")
								return "opacity: 0.1";
							})
						.attr("id", function(d){
							if (seatData[d.properties.name]["seat_info"]["change"] == "gain")
								return "gainfiltered"
						});

				if (gains == "nochange")
					g.selectAll("#partyfiltered")
						.attr("style", function(d) {
							if (seatData[d.properties.name]["party"] != "gain")
								return "opacity: 0.1";
							})
						.attr("id", function(d){
							if (seatData[d.properties.name]["seat_info"]["change"] == "hold")
								return "gainfiltered"
						});

			if (region == "null")
				g.selectAll("#gainfiltered")
					.attr("id", "regionfiltered")

			else
				g.selectAll("#gainfiltered")
					.attr("style", function(d){
						if (region != seatData[d.properties.name]["seat_info"]["area"])
							return "opacity: 0.1";
					})
					.attr("id", function(d){
						if (region == seatData[d.properties.name]["seat_info"]["area"])
							return "regionfiltered"
					});

			g.selectAll("#regionfiltered")
				.attr("style", function(d) {

					if (parseFloat(seatData[d.properties.name]["seat_info"]["majority_percentage"]) < majoritylow || parseFloat(seatData[d.properties.name]["seat_info"]["majority_percentage"]) > majorityhigh )
						return "opacity: 0.1"

					})
				.each(function(d) {
					if (parseFloat(seatData[d.properties.name]["seat_info"]["majority_percentage"]) >= majoritylow && parseFloat(seatData[d.properties.name]["seat_info"]["majority_percentage"]) <= majorityhigh )
						seatsAfterFilter.push(d);
					});



			g.selectAll(".map")
				.attr("id", function(d) {
					return "i" + seatData[d.properties.name]["seat_info"]["id"]
				});
			$("#totalfilteredseats").html(" ");
			$("#filteredlisttable").html(" ");

			generateSeatList();
		});

	}

// resets all filters and map to default state
function resetFilter(){

	filterStates[0].party = "null";
	filterStates[1].gain = "null";
	filterStates[2].region = "null";
	filterStates[3].majoritylow = 0;
	filterStates[4].majorityhigh = 100;

	filterMap("reset");



	$("#dropdownparty option:eq(0)").prop("selected", true);
	$("#dropdowngains option:eq(0)").prop("selected", true);
	$("#dropdownregion option:eq(0)").prop("selected", true);
	$("#majority").get(0).reset()

	$("#totalfilteredseats").html(" ");
	$("#filteredlisttable").html(" ");
}

// using seatsAfterFilter, generates list of filtered seats
function generateSeatList(){
			$("#totalfilteredseats").html(" ");
			$("#filteredlisttable").html(" ");

			filterToTicker = [];

			$("#totalfilteredseats").append("<p>Total : " + seatsAfterFilter.length + "</p>");
			$.each(seatsAfterFilter, function(i){

				// to modify ticker
				filterToTicker.push(seatsAfterFilter[i].properties.name)

				if (filterStates[1].gain == "gains" && filterStates[0].party != "null")
				$("#filteredlisttable").append("<tr class=" + seatData[seatsAfterFilter[i].properties.name]["seat_info"]["incumbent"] +
				"><td onclick=\"zoomToClickedFilteredSeat(seatsAfterFilter[" + i + "])\">" + seatsAfterFilter[i].properties.name + "</td></tr>");

				else
					$("#filteredlisttable").append("<tr class=" + seatData[seatsAfterFilter[i].properties.name]["seat_info"]["winning_party"] +
					"><td onclick=\"zoomToClickedFilteredSeat(seatsAfterFilter[" + i + "])\">" + seatsAfterFilter[i].properties.name + "</td></tr>");

			});

			activateTicker()
		}

// close to duplicate of clicked() due to slightly difference in data type used. fix at some point. this one is for generate seat list and seat search box
function zoomToClickedFilteredSeat(d){

	var id = "#i" + seatsToIDs[d.properties.name];
	previous = d3.select(previousnode);
	current = d3.select(id);

	repeat();

	// flashes selected seat on map
	function repeat(){

		previous.transition()
			.attr("opacity", 1)

		current
			.transition()
				.duration(1500)
				.attr("opacity", 0.2)
			.transition()
				.duration(1500)
				.attr("opacity", 1)
			.each("end", repeat);
		}

	var bounds = path.bounds(d),
		dx = Math.pow((bounds[1][0] - bounds[0][0]), 0.5),
		dy = Math.pow((bounds[1][1] - bounds[0][1]), 0.5),
		x = (bounds[0][0] + bounds[1][0]) / 2,
		y = (bounds[0][1] + bounds[1][1]) / 2,

		scale = .025 / Math.max(dx /  width,  dy / height),
		translate = [width / 2 - scale * x, height / 2 - scale * y];

	disableZoom();

	svg.transition()
			.duration(1500)
			.call(zoom.translate(translate).scale(scale).event)
			.each("end", enableZoom);

	seatinfo(d);
	previousnode = id;
	}


// stops users messing up zoom animation
function disableZoom(){
	svg.on("mousedown.zoom", null);
	svg.on("mousemove.zoom", null);
	svg.on("dblclick.zoom", null);
	svg.on("touchstart.zoom", null);
	svg.on("wheel.zoom", null);
	svg.on("mousewheel.zoom", null);
	svg.on("MozMousePixelScroll.zoom", null);
}

// reenables users ability to zoom pan etc
function enableZoom(){
	svg.call(zoom);
}

// reset button for map
function reset() {
	disableZoom();
	svg.transition()
		.duration(1500)
		.call(zoom.translate([0, 0]).scale(1).event)
		.each("end", enableZoom);

}

// zoom function
function zoomed() {
	g.style("stroke-width", 1.5 / d3.event.scale + "px");
	g.attr("transform", "translate(" + d3.event.translate + ")scale(" + d3.event.scale + ")");

}

function stopped() {
	if (d3.event.defaultPrevented) d3.event.stopPropagation();
}

// fill seat info in #right when seat clicked/selected

// to colour the information
var oldclass;

// fills out table of info at top of #right
function seatinfo(d){

	$("#information").removeClass(oldclass);
	$("#information").empty()

	// if declared
	//
	// add seat name = d.properties.name, region = regionlist[seat_info["area"]],  party name = partylist[seat_info["winning_party"]
	// if its a gain or no change =  seat_info["change"]
	// declaration time =  seat_info["declared_at"], majority = seat_info["majority_percentage"] + seat_info["majority_total"], turnout =
	// pie chart


	// else
	//
	// add seat name = d.properties.name, expected declaration time = seatDeclarations[d.properties.name]
	// clear pie chart + table

	if (d.properties.name in seatData){
		seat_info = seatData[d.properties.name]["seat_info"]

		$("#information").addClass(seat_info["winning_party"])

		// seatname and region
		$("#information").append("<p> " + d.properties.name + "<span style =\"float: right;\">" + regionlist[seat_info["area"]] + "</span></p>")

		var previousParty = ""
		if (seat_info["change"] == "gain"){
			previousParty = "from &nbsp&nbsp&nbsp" + "<span class=\"" + seat_info["incumbent"] + " partyinfospan \">" + partylist[seat_info["incumbent"]] + "</span>"
		}

		// party + hold/gain and from whom
		$("#information").append("<p>" + partylist[seat_info["winning_party"]] + "&nbsp&nbsp&nbsp"
											+ seat_info["change"].toUpperCase() + "&nbsp&nbsp&nbsp" + "<span>" + previousParty + "</span></p>")

		// majority + turnout
		$("#information").append("<p> Majority: " + seat_info["majority_total"] + " = " + seat_info["majority_percentage"]
												+ "% <span style =\"float: right;\"> Turnout: " + seat_info["percentage_turnout"] + "%</span></p>")

		// declaration time

		var onlyTime = seat_info["declared_at_simple"]

		$("#information").append("<p> Declared at:  " + onlyTime +  "</p>")

		//////////// CHANGE**///////////
		$("#information-pie").html(piechart(d));

		oldclass = seat_info["winning_party"]
	}
	else{
		$("#information").addClass("null")
		$("#information-pie").empty();
		$("#information-chart").empty();
		$("#information").append("<p>" + d.properties.name + "</p>")
		$("#information").append("<p> Expected Declaration Time : " + seatDeclarations[d.properties.name].substr(0, 5) + "</p>")
		$("#information").append("<p> Incumbent : <span class=\"" + predictions[d.properties.name].incumbent + " partyinfospan\">" + partylist[predictions[d.properties.name].incumbent] + "</span></p>")
		$("#information").append("<p> Predicted : <span class=\"" + predictions[d.properties.name].party + " partyinfospan\">" + partylist[predictions[d.properties.name].party] + "</span></p>")


		oldclass = "null"
	}
}

// d3 make pie chart of vote counts ni selected seat
function piechart(d){


	$("#information-pie").empty();
	$("#information-chart").empty();

	$("#information-chart").html("<p></p>");

	var data = [];

	//////////// CHANGE**///////////

	// for loop instead of awful shit before
	var relevant_party_info = seatData[d.properties.name]["party_info"]

	$.each(relevant_party_info, function(d){
		votes = relevant_party_info[d]["vote_percentage"]
		data.push({party: d, votes: votes, vote_change: relevant_party_info[d]["change_in_percentage"]})
	})

	var filterdata = [];

	$.each(data, function(i){

		if (data[i].votes > 0 && data[i].party != "other")
			filterdata.push(data[i]);
	});


	filterdata.sort(function(a, b){
			return b.votes - a.votes ;
	});

	//////////// CHANGE**///////////
	// different way of getting other into array (due to slightly difference in other in data)
	var keys = Object.keys(relevant_party_info)


	if (keys.indexOf("other") != -1){
		filterdata.push({party: "other", votes: relevant_party_info["other"]["vote_percentage"], vote_change: relevant_party_info["other"]["change_in_percentage"]})
	}


	var width = 225,
		height = 225;
		radius = Math.min(width, height) / 2;

	var arc = d3.svg.arc()
		.outerRadius(radius - 10)
		.innerRadius(0);

	var pie = d3.layout.pie()
		.sort(null)
		.value(function (d) {
			return d.votes;
		});

	var svg = d3.select("#information-pie").append("svg")
		.attr("width", width)
		.attr("height", height)
		.append("g")
		.attr("transform", "translate(" + width / 2 + "," + height / 2 + ")");

	var g = svg.selectAll(".arc")
			.data(pie(filterdata))
			.enter().append("g")
			.attr("class", "arc");

	g.append("path")
		.attr("d", arc)
		.attr("class", function(d, i){
				return filterdata[i].party;
			});


		//////////// CHANGE**///////////
		// different way of getting other into array replaced party name with candidate name (line 425)
	// creates table with vote counts/percentages


	$.each(filterdata, function(i){
		var plussign = "";
		if (filterdata[i].vote_change > 0){
			plussign = "+";
		}

		$("#information-chart").append("<tr class=" + filterdata[i].party + " style=\"font-weight: bold;\"><td>" +
			seatData[d.properties.name]["party_info"][filterdata[i].party]["name"] + "</td><td>" + (parseFloat(filterdata[i].votes)).toFixed(2) +  "%</td><td>"
			+ plussign + (parseFloat(filterdata[i].vote_change)).toFixed(2) +  "</td></tr>")
	})
}

// **CHANGE
// POSSIBLE COALITIONS

function possibleCoalitions(voteTotals){

	$("#coalitionlist").html("")
	data = {}
	$.each(voteTotals, function(i){
		data[voteTotals[i]["code"]] = voteTotals[i]["seats"]
	})

	var coalitions = [
										{"parties": "Lab/Lib",  "seats" : data["labour"] + data["libdems"]},
										{"parties": "Con*/Lib",  "seats" : data["conservative"] + data["libdems"]  + 1},
										{"parties": "Con*/UKIP",  "seats" : data["conservative"] + data["ukip"] + 1},
										{"parties": "Lab/SNP",  "seats" : data["labour"] + data["snp"]},
										{"parties": "Lab/Lib/SNP",  "seats" : data["labour"] + data["snp"] + data["libdems"]}
										]


	coalitions.sort(function(a, b){
			return b.seats - a.seats ;
	});

	$.each(coalitions, function(i){
		$("#coalitionlist").append(coalitions[i].parties + ":" + coalitions[i].seats + "\xA0\xA0\xA0\xA0\xA0")

	})




}

// load + colour map at page load
function loadmap(){

	var i = 0;

	d3.json("/election2015/data/projection.json", function(error, uk) {
		if (error) return console.error(error);

		g.selectAll(".map")
			.data(topojson.feature(uk, uk.objects.projection).features)
			.enter().append("path")
			.attr("class", function(d) {
				seatname = d.properties.name

				if (seatname in seatData){
					seatParty = seatData[seatname]["seat_info"]["winning_party"]
					return "map " + seatParty
				}
				else {
					return "not_here"
				}

				})
			.attr("opacity", 1)
			.attr("id", function(d) {
				if (d.properties.name in seatData){
					return "i" + seatData[d.properties.name]["seat_info"]["id"]
					}
				})
			.attr("d", path)
			.on("click", zoomToClickedFilteredSeat)
			.append("svg:title")
				.text(function(d) { return d.properties.name})
			.each(function(d){
				if (d.properties.name in seatData){
					seatsAfterFilter.push(d)
					filterToTicker.push(d.properties.name)
				}

				i += 1;
				searchSeatData.push(d)
				seatNames.push(d.properties.name);
				seatsToIDs[d.properties.name] = i

			});


			activateTicker();

	});
}

// add number with comma parser to tablesorter - not used in index/nowcast atm.
$( function() {
    $.tablesorter.addParser({
        id: "numberWithComma",
        is: function(s) {
            return /^[0-9]?[0-9,\.]*$/.test(s);
        },
        format: function(s) {
            return $.tablesorter.formatFloat(s.replace(/,/g, ''));
        },
        type: "numeric"
    });
});

// when user selects region this removes old vote list and replaces with selection
// problem with cache of tablesorter not being cleared result in multiple tables being displayed
// just rewriting the html as fix
function displayVoteTotals(data) {


		///////// CHANGE**
		//Columns 3 and 4 were formerly Vote% and Vote%Change, now they are Votes and Vote%
		//objects that populate these tables are in an identical format just .votepercentchange is replaced by .votes
		$("#totalstable").remove()
		$("#totals").append("<table id=\"totalstable\" class=\"tablesorter\"><thead><tr><th>Party</th>" +
												"<th class=\"tablesorter-header\">Seats</th><th class=\"tablesorter-header\">Change</th>" +
												"<th class=\"tablesorter-header\">Votes</th><th class=\"tablesorter-header\">Vote %</th>" +
												"</tr></thead><tbody id=\"totalstableinfo\"></tbody><tfoot id=\"totalstablefoot\">" +
												"</tfoot></table>")

		var plussign1, plussign2;

		$.each(data, function(i){

				if (data[i].change > 0){
					plussign1 = "+";
				}
				else{
					plussign1 = "";
				}

			if (i == data.length -1){
					null
				}

			else if (i == data.length -2){
				$("#totalstablefoot").append("<tr class=\"" + data[i].code +"\"><td>" + partylist[data[i].code] + "</td><td>"
					+ data[i].seats + "</td><td>" + data[i].change + "</td><td>" + data[i].votes + "</td><td>" + (data[i].votepercent).toFixed(2) +
					 "</td></tr>");
				}
			else{
				if (data[i].votepercent > 0)
					$("#totalstableinfo").append("<tr class=\"" + data[i].code +"\"><td>" + partylist[data[i].code] + "</td><td>"
						+ data[i].seats + "</td><td>"  + plussign1 + data[i].change + "</td><td>" + data[i].votes + "</td><td>" + (data[i].votepercent).toFixed(2) +
					 "</td></tr>");
				}

		})



		$("#totalstable").tablesorter({

				sortInitialOrder: "asc",
				headers: {
					1: { sortInitialOrder: 'desc' },
					2: { sortInitialOrder: 'desc' },
					4: { sortInitialOrder: 'desc' },
					0: {
						sorter: false
					}
			},
				sortList:[[3,1]]
		});
};

// autocomplete search box, references array generated on page load

function searchSeats(value){
	$.each(searchSeatData, function(i){
		if (searchSeatData[i].properties.name == value)
			zoomToClickedFilteredSeat(searchSeatData[i])
	});
};

// autocoomplete function
$(function()
{
	$("#searchseats").autocomplete({
		source: seatNames,
		select: function(event, ui){
			searchSeats(ui.item.label);
		}
	});
});

//seatData contains all information display on page. filled on page load using getSeatInfo
var seatData = {};

//empty arrays for data for each regional vote total

nationalVoteTotals = [];
greatbritainVoteTotals = [];
englandVoteTotals = [];
scotlandVoteTotals = [];
walesVoteTotals = [];
northernirelandVoteTotals = [];
northeastenglandVoteTotals = [];
northwestenglandVoteTotals = [];
westmidlandsVoteTotals = [];
eastmidlandsVoteTotals = [];
yorkshireandthehumberVoteTotals = [];
eastofenglandVoteTotals = [];
southeastenglandVoteTotals = [];
southwestenglandVoteTotals = [];
londonVoteTotals = [];




//user eslect region vote totals
function selectAreaInfo(value){

	if (value == "country") {displayVoteTotals(nationalVoteTotals)};
	if (value == "england") {displayVoteTotals(englandVoteTotals)};
	if (value == "scotland") {displayVoteTotals(scotlandVoteTotals)};
	if (value == "eastofengland") {displayVoteTotals(eastofenglandVoteTotals)};
	if (value == "northeastengland") {displayVoteTotals(northeastenglandVoteTotals)};
	if (value == "northwestengland") {displayVoteTotals(northwestenglandVoteTotals)};
	if (value == "southwestengland") {displayVoteTotals(southwestenglandVoteTotals)};
	if (value == "southeastengland") {displayVoteTotals(southeastenglandVoteTotals)};
	if (value == "london") {displayVoteTotals(londonVoteTotals)};
	if (value == "wales") {displayVoteTotals(walesVoteTotals)};
	if (value == "northernireland") {displayVoteTotals(northernirelandVoteTotals)};
	if (value == "yorkshireandthehumber") {displayVoteTotals(yorkshireandthehumberVoteTotals)};
	if (value == "eastmidlands") {displayVoteTotals(eastmidlandsVoteTotals)};
	if (value == "westmidlands") {displayVoteTotals(westmidlandsVoteTotals)};
	if (value == "greatbritain") {displayVoteTotals(greatbritainVoteTotals)};
}

///////// CHANGE**
// everything below here is vastly different but probably not relevant

function getData(){

	return $.ajax({
		dataType: "json",
  	url: "info.json",
		type: "GET",

	});
}


$.ajaxSetup({ cache: false });


function getSeatInfo(data){

  $.each(data, function(seat){
		seatData[seat] = data[seat]
	})
	loadmap()

	areas = regions["england"].concat(regions["scotland"]).concat(regions["wales"]).concat(regions["northernireland"]);

	getVoteTotals("all")

	getVoteTotals("greatbritain")
	getVoteTotals("england")

	for (area in areas){
		getVoteTotals(areas[area])
	}
	displayVoteTotals(nationalVoteTotals)
	possibleCoalitions(nationalVoteTotals)
	pageRefreshTotal += 1

}

// call the function
getData().done(getSeatInfo);
var seatDeclarations = {};

// get predictions for none declared seats
var predictions = {};

$.getJSON("predictions.json", function(data){
	$.each(data, function(seat){
		predictions[seat] = {incumbent : data[seat]["incumbent"], party : data[seat]["party"] }
	})
});


$.getJSON("seat_declaration_times.json", function(seats){
	$.each(seats, function(seat) {
		seatDeclarations[seat] = seats[seat]
	});
});

var regions = {
    "england"  : ["northeastengland", "northwestengland", "yorkshireandthehumber", "southeastengland", "southwestengland", "eastofengland",
                  "eastmidlands", "westmidlands", "london"],
    "scotland"  : ["scotland"],
    "wales" : ["wales"],
    "northernireland" : ["northernireland"]
};

parties = ["conservative", "labour", "libdems", "ukip", "snp", "plaidcymru", "green", "uu", "sdlp", "dup", "sinnfein", "alliance", "other1", "other2"]


function getVoteTotals(area){

    var areas = [];

    if (area == "all"){
      areas = regions["england"].concat(regions["scotland"]).concat(regions["wales"]).concat(regions["northernireland"]);
    }

    else if (area == "greatbritain"){
      areas = regions["england"].concat(regions["scotland"]).concat(regions["wales"]);
    }

    else if (area == "england"){
      areas = regions["england"];
    }
    else {
      areas.push(area);
    }

		var totalvotescast = 0

		$.each(seatData, function(seat){

			if (areas.indexOf(seatData[seat]["seat_info"]["area"]) != -1 ){
				totalvotescast += parseInt(seatData[seat]["seat_info"]["turnout"]);
			}
		});

		var holdingArray = []
		var totalseats = 0;
		var totalvotes = 0;

		var otherSeatssum = 0;
		var otherChange = 0;
		var otherTotalVotes = 0
		var otherVotePercent = 0

		$.each(parties, function(party){
			info = {}
			var code = parties[party];
			if (code == "other1" || code == "other2"){
				code = "other"
			}
			var seatssum = 0;
			var change = 0;
			var totalvotes = 0;
			var votepercent = 0;

			$.each(seatData, function(seat){

				if (areas.indexOf(seatData[seat]["seat_info"]["area"]) > -1){


					if (seatData[seat]["seat_info"]["winning_party"] == code){
						seatssum += 1;
						totalseats += 1;

					}


					if (seatData[seat]["seat_info"]["incumbent"] == code){
						change += 1;
					}

					parties_in_seat = Object.keys(seatData[seat]["party_info"])

					if (parties_in_seat.indexOf(parties[party]) != -1) {
						totalvotes += seatData[seat]["party_info"][parties[party]]["vote_total"];
					}


					}
				})


			votepercent =  parseFloat((100 * totalvotes / parseFloat(totalvotescast)).toFixed(2));
			change = seatssum - change;

			if (code == "other"){
				otherSeatssum = seatssum;
				otherChange = change;
				otherTotalVotes +=totalvotes;
				otherVotePercent += votepercent

			}


			else {
				info["code"] = code;
				info["seats"] = seatssum;
				info["change"] = change;
				info["votes"] = totalvotes;
				info["votepercent"] = votepercent;

				holdingArray.push(info);
			}

		});

		var others = {"code": "other", "seats" : otherSeatssum, "change": otherChange, "votes": otherTotalVotes, "votepercent" : otherVotePercent};
		var totals = {"code": "total", "seats" : totalseats - otherSeatssum, "change": "", "votes": totalvotescast, "votepercent" : 100.00};
		var stupidcsvextrarow = {"code": "", "seats" : undefined, "change": undefined, "votes" : "", "votepercent" : undefined};



		holdingArray.push(others)
    holdingArray.push(totals);
    holdingArray.push(stupidcsvextrarow);

    alterTable(area, holdingArray);

}


function alterTable(area, holdingarray){

  if (area == "all"){
    nationalVoteTotals = holdingarray;

		title = "Election LIVE "

		$.each(nationalVoteTotals, function(i){
			if (nationalVoteTotals[i].code == "total"){
				title += nationalVoteTotals[i].seats + "/650"
			}
		})

		$.each(nationalVoteTotals, function(i){
			if (nationalVoteTotals[i].code == "conservative"){
				title += " CON " + nationalVoteTotals[i].seats
			}
			if (nationalVoteTotals[i].code == "labour"){
				title += " LAB " + nationalVoteTotals[i].seats
			}
			if (nationalVoteTotals[i].code == "libdems"){
				title += " LIB " + nationalVoteTotals[i].seats
			}
			if (nationalVoteTotals[i].code == "ukip"){
				title += " UKIP " + nationalVoteTotals[i].seats
			}
			if (nationalVoteTotals[i].code == "snp"){
				title += " SNP " + nationalVoteTotals[i].seats
			}

		})

		document.title = title;
  }

  if (area == "greatbritain"){
    greatbritainVoteTotals = holdingarray;
  }

  if (area == "england"){
    englandVoteTotals = holdingarray;
  }

  if (area == "scotland"){
    scotlandVoteTotals = holdingarray;
  }

  if (area == "wales"){
    walesVoteTotals = holdingarray;
  }

  if (area == "northernireland"){
    northernirelandVoteTotals = holdingarray;
  }

  if (area == "northeastengland"){
    northeastenglandVoteTotals = holdingarray;
  }

  if (area == "northwestengland"){
    northwestenglandVoteTotals = holdingarray;
  }

  if (area == "westmidlands"){
    westmidlandsVoteTotals = holdingarray;
  }


  if (area == "eastmidlands"){
    eastmidlandsVoteTotals = holdingarray;
  }

  if (area == "yorkshireandthehumber"){
    yorkshireandthehumberVoteTotals = holdingarray;
  }

  if (area == "eastofengland"){
    eastofenglandVoteTotals = holdingarray;
  }

  if (area == "southeastengland"){
    southeastenglandVoteTotals = holdingarray;
  }

  if (area == "southwestengland"){
    southwestenglandVoteTotals = holdingarray;
  }

  if (area == "london"){
    londonVoteTotals = holdingarray;
  }
}



// auto refresh elements

function autoRefresh () {

	var refreshRate = 90000;

	var today = new Date();
	var dd = today.getDate();
	var mm = today.getMonth() + 1;
	var hh = today.getHours();


	// alter refresh delay rate based on time -


	//for night itself - 11-12: 90s, 12-2 : 60s, 2-5: 30s, 5-7: 60s, 7-: 90 seconds
	//
	// if (mm == 5 && dd == 8){
	//
	// 	if (hh >= 0 && hh < 2){
	// 		refreshRate = 60000;
	// 	}
	//
	// 	if (hh >= 2 && hh < 5){
	// 		refreshRate = 30000;
	// 	}
	//
	// 	if (hh >= 5 && hh < 7){
	// 		refreshRate = 60000;
	// 	}
	//
	// }

	//for test

	if (mm == 4 && dd == 18){

		if (hh >= 11 && hh < 12){
			refreshRate = 60000;
		}

		if (hh >= 12 && hh < 15){
			refreshRate = 30000;
		}

		if (hh >= 15 && hh < 17){
			refreshRate = 60000;
		}

	}

	if (!(refreshState)) {
		//	console.log("not refreshing")
			$("#refreshbutton").remove()
			return
		}

	else {
		//console.log("Refresh Rate", refreshRate/1000, "s");


		setTimeout(function () {
			//window.location.reload(true)
			// remove old map - buggy otherwise
			$("svg .map").remove()
			$("svg .not_here").remove()
			// reacquire data + reload map
			seatsAfterFilter = []; // for use with user inputs in filters - changing map opacity + generating seat list at end
			searchSeatData = []; // for use with search box
			seatNames = []; // for use with search box
			seatData = {};
			seatInfoForTicker = [];
			filterToTicker = [];
			seatsToIDs = {};

			resetFilter()
			$("#selectareatotals option:eq(0)").prop("selected", true);

			getData().done(getSeatInfo);

			//console.log(Date())

			autoRefresh();

		}, refreshRate )//x / 1000 = seconds
		}
}

var refreshState = true;
autoRefresh();


var seatInfoForTicker = [];
// var gainSeats = [];

function activateTicker(){

	seatInfoForTicker = [];

	$(".tickerSeats").remove();

	d3.selectAll(".map")
		.each(function(d){

			seat = d.properties.name
			geometry = d

			var declaredAt = Date.parse(seatData[seat]["seat_info"]["declared_at"])
			var declaredAtSimple = seatData[seat]["seat_info"]["declared_at_simple"]



			// for flash gained seats
			// if (seatData[seat]["seat_info"]["change"] == "gain"){
			// 	gainedSeats.push({pf_id: seatData[seat]["seat_info"]["id"]})
			// }
			if (filterToTicker.indexOf(seat) != -1){

				seatInfoForTicker.push({"name" : seat,
																"declared_at" : declaredAt,
																"declared_at_simple" : declaredAtSimple,
																"winning_party" : seatData[seat]["seat_info"]["winning_party"],
																"change" : seatData[seat]["seat_info"]["change"],
																"geometry" : geometry
																	})
					}
			})


		seatInfoForTicker.sort(function(a, b){
				return b.declared_at - a.declared_at;
		});

		$.each(seatInfoForTicker, function(i){
			var seatinfo = seatInfoForTicker[i]


			//
			// var onlyGainedSeats = "";
			// if (seatinfo.change == "hold"){
			// 	var onlyGainedSeats = "tickerGainSeats "
			// }


			$("#ticker").append("<tr id=\"ticker" + seatData[seatinfo.name]["seat_info"]["id"] + "\" class=\"tickerSeats "  + seatinfo.winning_party + "\" onclick=\"zoomToClickedFilteredSeat(seatInfoForTicker[" + i + "].geometry)\">" +
													"<td style=\"padding-right: 8px\";>" + seatinfo.declared_at_simple + "</td ><td style=\"padding-right: 8px; width: 100%;\">"
													+ seatinfo.name + "</td><td style=\"padding-right: 8px\">"
													+ seatinfo.change.toUpperCase()	+ "</td></tr>")

			if (currentSeats.indexOf(seatinfo.name) == -1){
				currentSeats.push(seatinfo.name)
				var id = "#ticker" + seatData[seatinfo.name]["seat_info"]["id"]

				if (pageRefreshTotal > 1 && !(isIE)){
					$(id).fadeOut(2000).fadeIn(2000).fadeOut(2000).fadeIn(2000).fadeOut(2000).fadeIn(2000);

				}
			}
		});
}


// for browsers
var isFirefox = typeof InstallTrigger !== 'undefined';
var isIE = /*@cc_on!@*/false || !!document.documentMode;

// // $(document).ready(function(){
// //   if (isFirefox == true || isIE == true){
// // 		$("#refreshbutton").css("margin-left", "795px")
// 	  }
//
// });


// flashGainsState = false;
//
// function flashGains(){
// 	if (flashGainsState == false){
// 		$(".tickerGainSeats").attr("style", "opacity:0.1;")
// 		flashGainsState = true
// 		}
//
// 	else {
// 		$(".tickerGainSeats").attr("style", "opacity:1;")
// 		flashGainsState = false
// 	}
//
//
// 		// var id = "#i" + gainedSeats[i].pf_id
// 		// current = d3.select(id)
// 		//
// 		// console.log(current)
// 		// repeat();
// 		//
// 		// function repeat(){
// 		// 	current
// 		// 		.transition()
// 		// 			.duration(4000)
// 		// 			.attr("opacity", 0.2)
// 		// 		.transition()
// 		// 			.duration(4000)
// 		// 			.attr("opacity", 1)
// 		// 	}
//
// }

function DO_NOT_PRESS(){


	d3.selectAll(".map")
		.attr("class", "map ukip");


	d3.selectAll(".not_here")
		.attr("class", "map ukip");

	alert("Why would you do this?");

	$("#right").append("<div id=\"farage\" style =\"position: absolute\"><img style=\"position: relative; z=index: -1; height: 800px; width: 600px;\" src=\"farage.png\"></div>");





		setTimeout(function () {
			window.location.reload(true)



		}, 10000 )//x / 1000 = seconds


}
