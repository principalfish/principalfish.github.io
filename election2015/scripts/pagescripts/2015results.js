



// for live

/////ideas

// NOT DONE

//DONE
// alter refresh delay rate based on time - for night itself - 11-12: 90s, 12-2 : 60 seconds, 2-5: 30s, 5-7: 60s, 7-: 120 seconds
// make css less shit - maybe
// for refreshing, redo functions getseatinfo + loadmap + displayVoteTotals(nationalVoteTotals) every minute or so
// initiate while loop when user on site - delay of 60 seconds

// live ticker where user inputs was
// smilar while loop + refresh
// lists seats in order of declaration time
// scrollable + clickable
// possible coalitions

// check if browser is re d/ling livemap.json every refresh - it isn't now...i think
// can remove d3.json("livemap") in filterMap? check thoroguhky - seems to remove filteredlist
// also reset doesnt - > filterlist...not a bad thing?
// ISSUE  - if filter is selected  - on refresh .not_here won't flash - because its faded not here- change

// 1 seat = 1.4ish kb of data
// load info in from new_info.json file (last 5 minutes of data?) - append to seatData
// when refreshing, will require redo of loadmap function...maybe?


//////////// CHANGE**///////////
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


// current state of user input filters
var filterStates = [{party: "null"}, {gain:"null"}, {region: "null"}, {majoritylow : 0}, {majorityhigh : 100}];

// empty arrays for various data
var seatsAfterFilter = []; // for use with user inputs in filters - changing map opacity + generating seat list at end
var searchSeatData = []; // for use with search box
var seatNames = []; // for use with search box

var currentSeats = []; // for use flashing new se
var totalElectorate = 0;
var oldElectorate = 0;
var previousTotals = {
	"eastmidlands": 2180243,
	"eastofengland": 2871212,
	"london": 3348875,
	"northeastengland": 1161554,
	"northernireland": 661055,
	"northwestengland": 3205582,
	"scotland": 2456365,
	"southeastengland": 4274287,
	"southwestengland": 2773443,
	"wales": 1441758,
	"westmidlands": 2640572,
	"yorkshireandthehumber": 2368363
}

var swingState = ["null", "null"]
var partyVoteShare = "null"
var partyVoteShareChange = "null"
var seatsFromIDs = {};


// control flow for analysing user filter inputs
function filterMap(setting){

	d3.selectAll(".map")
		.attr("opacity", 1)

	$("#keyonmap").html("");

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
	if (setting == "reset"){
		g.selectAll(".faded_not_here")
			.attr("class", "not_here");
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

			if (parseFloat(seatData[d.properties.name]["seat_info"]["maj_percent"]) < majoritylow || parseFloat(seatData[d.properties.name]["seat_info"]["maj_percent"]) > majorityhigh )
				return "opacity: 0.1"

			})
		.each(function(d) {
			if (parseFloat(seatData[d.properties.name]["seat_info"]["maj_percent"]) >= majoritylow && parseFloat(seatData[d.properties.name]["seat_info"]["maj_percent"]) <= majorityhigh )
				seatsAfterFilter.push(d);
			});



	g.selectAll(".map")
		.attr("id", function(d) {
			return "i" + d.properties.info_id
		});


	generateSeatList();


	}

// resets all filters and map to default state
function resetFilter(){

	filterStates[0].party = "null";
	filterStates[1].gain = "null";
	filterStates[2].region = "null";
	filterStates[3].majoritylow = 0;
	filterStates[4].majorityhigh = 100;

	$.each(seatData, function(seat){
		seatData[seat]["seat_info"]["current_colour"] = 1;
	})

	var previous_opacity = 1;
	var current_colour = 1;

	swingState = ["null", "null"];

	partyVoteShare = "null"
	partyVoteShareChange = "null"

	filterMap("reset");

	$("#votesharebypartyselect option:eq(0)").prop("selected", true);
	$("#votesharechangebypartyselect option:eq(0)").prop("selected", true);	
	$("#swingfrom option:eq(0)").prop("selected", true);
	$("#swingto option:eq(0)").prop("selected", true);

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

			$("#totalfilteredseats").append("<p>Total : " + seatsAfterFilter.length + "</p>");
			$.each(seatsAfterFilter, function(i){

				// to modify ticker


				if (filterStates[1].gain == "gains" && filterStates[0].party != "null")
				$("#filteredlisttable").append("<tr class=" + seatData[seatsAfterFilter[i].properties.name]["seat_info"]["incumbent"] +
				"><td onclick=\"zoomToClickedFilteredSeat(seatsAfterFilter[" + i + "])\">" + seatsAfterFilter[i].properties.name + "</td></tr>");

				else
					$("#filteredlisttable").append("<tr class=" + seatData[seatsAfterFilter[i].properties.name]["seat_info"]["winning_party"] +
					"><td onclick=\"zoomToClickedFilteredSeat(seatsAfterFilter[" + i + "])\">" + seatsAfterFilter[i].properties.name + "</td></tr>");

			});

			// activateTicker()
		}

var previous_opacity ;
var current_colour;

function zoomToClickedFilteredSeat(d){

	var id = "#i" + d.properties.info_id;


	if (previousnode != undefined){
		var previous_seat = seatsFromIDs[previousnode]
		previous_opacity = seatData[previous_seat]["seat_info"]["current_colour"]

		}

	if (previous_opacity == undefined){
		previous_opacity = 1;
	}


	var current_seat = seatsFromIDs[id]
	current_colour = seatData[current_seat]["seat_info"]["current_colour"]

	if (current_colour == undefined){
		current_colour = 1;
	}

	previous = d3.select(previousnode);
	current = d3.select(id);

	repeat();

	previous.transition()
		.attr("opacity", previous_opacity)
	// flashes selected seat on map
	function repeat(){
		current
			.transition()
				.duration(1500)
				.attr("opacity", 0.2)
			.transition()
				.duration(1500)
				.attr("opacity", current_colour)
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

//	$("#information").removeClass(oldclass);
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

		if (seat_info["incumbent"] == "speaker" || seat_info["incumbent"] == "independent"){
			seat_info["incumbent"] = "other"
		}

		if (seat_info["incumbent"] == seat_info["winning_party"]){
			seat_info["change"] = "hold"
		}

		//$("#information").addClass(seat_info["winning_party"])

		// seatname and region
		$("#information").append("<h3> " + d.properties.name + "<span style =\"float: right;\">" + regionlist[seat_info["area"]] + "</span></h3>")

		var previousParty = ""
		if (seat_info["change"] == "gain"){
			previousParty = "from &nbsp&nbsp&nbsp" + "<span class=\"" + seat_info["incumbent"] + " partyinfospan \">" + partylist[seat_info["incumbent"]] + "</span>"
		}

		// party + hold/gain and from whom
		$("#information").append("<h3><span class=\"partyinfospan " + seat_info["winning_party"] +  "\">" + partylist[seat_info["winning_party"]] + "&nbsp&nbsp&nbsp"
											+ seat_info["change"].toUpperCase() + "</span>&nbsp&nbsp&nbsp" + "<span>" + previousParty + "</span></h3>")

		// majority + turnout
		$("#information").append("<h3> Majority: " + seat_info["maj"] + " = " + seat_info["maj_percent"].toFixed(2)
												+ "% <span style =\"float: right;\"> Turnout: " + seat_info["percentage_turnout"].toFixed(2) + "%</span></h3>")

		// declaration time

		var onlyTime = seat_info["declared"]

	//	$("#information").append("<h3> Declared at:  " + onlyTime +  "</h3>")

		//////////// CHANGE**///////////
		$("#information-pie").html(piechart(d));

		oldclass = seat_info["winning_party"]
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
		votes = relevant_party_info[d]["percent"]
		if (votes > 0){
			data.push({party: d, votes: votes, vote_change: relevant_party_info[d]["change"]})
		}

	})

	var filterdata = [];

	$.each(data, function(i){
		if (data[i].votes > 0 && data[i].party != "others")
			filterdata.push(data[i]);
	});

	filterdata.sort(function(a, b){
			return b.votes - a.votes ;
	});

	//////////// CHANGE**///////////
	// different way of getting other into array (due to slightly difference in other in data)
	var keys = Object.keys(relevant_party_info);

	if (keys.indexOf("others") != -1 && relevant_party_info["others"]["percent"] > 0){
		filterdata.push({party: "others", votes: relevant_party_info["others"]["percent"], vote_change: relevant_party_info["others"]["change"]});
	}

	// BAR CHART

	var barchartdata = [];
	$.each(filterdata, function(i){
		if (filterdata[i].votes >= 3){
			barchartdata.push(filterdata[i]);
		}
	});

	var dataitems = barchartdata.length
	var margin = {top: 10, right: 0, bottom: 10, left: 25};

	var width = 300 - margin.left - margin.right;
	var height = 225 - margin.top - margin.bottom;
	var bargap = 2;
	var barwidth = d3.min([60, (width / dataitems) - bargap]);
	var animationdelay = 250;

	var svg1 = d3.select("#information-pie")
		.append("svg")
			.attr("width", width + margin.left + margin.right)
			.attr("height", height + margin.top + margin.bottom)
		.append("g")
    	.attr("transform", "translate(" + margin.left + "," + margin.top + ")");

	var x = d3.scale.ordinal().range([0, dataitems * (barwidth + 2)]);

	var xAxis = d3.svg.axis()
	    .scale(x)
	    .orient("bottom");

	var y = d3.scale.linear()
		.range([height, 0])

	var yAxis = d3.svg.axis()
    .scale(y)
    .orient("left")
    .ticks(6);

	var max_of_votes = d3.max(barchartdata, function(d) { return d.votes; })
	y.domain([0, d3.max([60, (max_of_votes + (10 - max_of_votes % 10))])]);

	svg1.append("g")
      .attr("class", "x axis")
      .attr("transform", "translate(0," + height + ")")
      .call(xAxis);

	svg1.append("g")
    .attr("class", "y axis")
    .call(yAxis);

	svg1.selectAll("rect")
		.data(barchartdata)
		.enter()
		.append("rect")
      .attr("x", function(d, i) { return bargap * (i + 1) + barwidth * (i); })
      .attr("width", barwidth)
			.attr("y", height)
      .attr("height", 0)
			.attr("class", function(d) { return d.party;})
		.transition()
      .delay(function(d, i) { return i * animationdelay / 2; })
      .duration(animationdelay)
      .attr("y", function(d) { return y(d.votes); })
      .attr("height", function(d) { return height - y(d.votes); });


	// PIE CHART
	// var width = 225,
	// 	height = 225;
	// 	radius = Math.min(width, height) / 2;
	//
	// var arc = d3.svg.arc()
	// 	.outerRadius(radius - 10)
	// 	.innerRadius(0);
	//
	// var pie = d3.layout.pie()
	// 	.sort(null)
	// 	.value(function (d) {
	// 		return d.votes;
	// 	});
	//
	// var svg = d3.select("#information-pie").append("svg")
	// 	.attr("width", width)
	// 	.attr("height", height)
	// 	.append("g")
	// 	.attr("transform", "translate(" + width / 2 + "," + height / 2 + ")");
	//
	// var g = svg.selectAll(".arc")
	// 		.data(pie(filterdata))
	// 		.enter().append("g")
	// 		.attr("class", "arc");
	//
	// g.append("path")
	// 	.attr("d", arc)
	// 	.attr("class", function(d, i){
	// 			return filterdata[i].party;
	// 		});
		//////////// CHANGE**///////////
		// different way of getting other into array replaced party name with candidate name (line 425)
	// creates table with vote counts/percentages


	$.each(filterdata, function(i){
		var plussign = "";
		if (filterdata[i].vote_change > 0){
			plussign = "+";
		}

		var vote_change;
		if (filterdata[i].vote_change ==""){
			vote_change = "";
		}
		else {
			vote_change = parseFloat(filterdata[i].vote_change).toFixed(2);
		}

		$("#information-chart").append("<tr class=" + filterdata[i].party + " style=\"font-weight: bold;\"><td style=\"max-width: 170px;\">" +
			seatData[d.properties.name]["party_info"][filterdata[i].party]["name"] + "</td><td>" + (parseFloat(filterdata[i].votes)).toFixed(2) +  "%</td><td>"
			+ plussign + vote_change +  "</td></tr>")
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
										{"parties": "Con/Lib",  "seats" : data["conservative"] + data["libdems"]},
										{"parties": "Con/UKIP",  "seats" : data["conservative"] + data["ukip"]},
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

	d3.json("2015resultsfiles/livemap.json", function(error, uk) {
		if (error) return console.error(error);

		g.selectAll(".map")
			.data(topojson.feature(uk, uk.objects.map).features)
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
				return "i" + d.properties.info_id
				})
			.attr("d", path)
			.on("click", zoomToClickedFilteredSeat)
			.append("svg:title")
				.text(function(d) { return d.properties.name})
			.each(function(d){
				if (d.properties.name in seatData){
					seatsAfterFilter.push(d);

					}
				searchSeatData.push(d);
				seatNames.push(d.properties.name);
				seatsFromIDs["#i" + d.properties.info_id] = d.properties.name


			});

		g.append("path")
			.datum(topojson.mesh(uk, uk.objects.map, function(a, b){
				return seatData[a.properties.name]["seat_info"]["area"] != seatData[b.properties.name]["seat_info"]["area"] && a != b; }))
			.attr("d", path)
			.attr("class", "boundaries");



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
												"	<th class=\"tablesorter-header\">% +/-</th></tr></thead><tbody id=\"totalstableinfo\"></tbody><tfoot id=\"totalstablefoot\">" +
												"</tfoot></table>")

		var other_change = 0
		$.each(data, function(i){

				if (parties.indexOf(data[i].code) != -1 && data[i].code != "other" ){
					other_change -= data[i].change
				}
		})

		data[data.length - 3].change = other_change



		var plussign1, plussign2;
		$.each(data, function(i){

				var percentchange;
				if (data[i].percentchange != undefined){
					percentchange = data[i].percentchange.toFixed(2);

					if (percentchange > 0) {
						percentchange = "+" + percentchange
					}

				}

				else {
					percentchange = ""
				}

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
				$("#totalstablefoot").append("<tr style=\"text-align: center;\" class=\"" + data[i].code +"\"><td style=\"text-align: left;\">" + partylist[data[i].code] + "</td><td>"
					+ data[i].seats + "</td><td>" + data[i].change + "</td><td style=\"text-align: right;\">" + data[i].votes.toLocaleString() + "</td><td>" + (data[i].votepercent) +
					 "</td><td></td></tr>");
				}
			else{
				if (data[i].votepercent > 0)
					$("#totalstableinfo").append("<tr style=\"text-align: center;\" class=\"" + data[i].code +"\"><td style=\"text-align: left;\">" + partylist[data[i].code] + "</td><td>"
						+ data[i].seats + "</td><td>"  + plussign1 + data[i].change + "</td><td style=\"text-align: right;\">" + data[i].votes.toLocaleString() + "</td><td>" + (data[i].votepercent).toFixed(2) +
					 "</td><td>" + percentchange + "</td></tr>");
				}
		})

		$("#totalstable").tablesorter({

				sortInitialOrder: "asc",
				headers: {
					3: { sortInitialOrder: 'desc' },
					2: { sortInitialOrder: 'desc' },
					4: { sortInitialOrder: 'desc' },
					5: { sortInitialOrder: 'desc' },
					0: {
						sorter: false
					}
			},
				sortList:[[1,1], [3, 1]]
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

function getData(url){

	return $.ajax({
		cache: false,
		dataType: "json",
  	url: url,
		type: "GET",
	});
}

function getSeatInfo(data){

  $.each(data, function(seat){
		if (!(seat in seatData)){
			seatData[seat] = data[seat];
			totalElectorate += data[seat]["seat_info"]["electorate"];
			oldElectorate += data[seat]["seat_info"]["old_electorate"]
		}
	})
	loadmap();

	areas = regions["england"].concat(regions["scotland"]).concat(regions["wales"]).concat(regions["northernireland"]);

	getVoteTotals("all");

	getVoteTotals("greatbritain");
	getVoteTotals("england");

	for (area in areas){
		getVoteTotals(areas[area]);
	}
	displayVoteTotals(nationalVoteTotals);
	possibleCoalitions(nationalVoteTotals);


	var totalTurnout = 100 * nationalVoteTotals[nationalVoteTotals.length - 2].votes / totalElectorate ;
	if (isNaN(totalTurnout)){
		totalTurnout = 100;
	}
	totalTurnout = "Turnout : " + String(totalTurnout.toFixed(2)) + "%"
	document.getElementById("totalturnout").innerHTML = totalTurnout
}

// call the function
$(document).ready(function(){ getData("2015resultsfiles/info.json").done(getSeatInfo)});



var regions = {
    "england"  : ["northeastengland", "northwestengland", "yorkshireandthehumber", "southeastengland", "southwestengland", "eastofengland",
                  "eastmidlands", "westmidlands", "london"],
    "scotland"  : ["scotland"],
    "wales" : ["wales"],
    "northernireland" : ["northernireland"]
};

parties = ["conservative", "labour", "libdems", "ukip", "snp", "plaidcymru", "green", "uu", "sdlp", "dup", "sinnfein", "alliance", "other", "others"]


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



		var totalvotescast = 0;

		var oldturnout = 0;

		$.each(seatData, function(seat){

			if (areas.indexOf(seatData[seat]["seat_info"]["area"]) != -1 ){
				totalvotescast += parseInt(seatData[seat]["seat_info"]["turnout"]);

			}
		});

		$.each(areas, function(area){
			oldturnout +=  previousTotals[areas[area]]
		})

		var holdingArray = [];
		var totalseats = 0;
		var totalvotes = 0;

		var otherSeatssum = 0;
		var otherChange = 0;
		var otherTotalVotes = 0;
		var otherVotePercent = 0;

		$.each(parties, function(party){
			info = {}
			var code = parties[party];
			if (code == "other" || code == "others"){
				code = "other"
			}
			var seatssum = 0;
			var change = 0;
			var totalvotes = 0;
			var votepercent = 0;
			var oldvotetotal = 0;
			var old_vote_percent


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
						totalvotes += seatData[seat]["party_info"][parties[party]]["total"];
						oldvotetotal += seatData[seat]["party_info"][parties[party]]["old"]
					}

					}
				});

			votepercent =  parseFloat((100 * totalvotes / parseFloat(totalvotescast)).toFixed(2));
			old_vote_percent =  parseFloat((100 * oldvotetotal / parseFloat(oldturnout)).toFixed(2));
			var vote_percent_change = votepercent - old_vote_percent

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
				info["percentchange"] = vote_percent_change;
				holdingArray.push(info);
			}
		});

		var others = {"code": "other", "seats" : otherSeatssum, "change": 0, "votes": otherTotalVotes, "votepercent" : otherVotePercent};
		var totals = {"code": "total", "seats" : totalseats - otherSeatssum, "change": "", "votes": totalvotescast, "votepercent" : " "};
		var stupidcsvextrarow = {"code": "", "seats" : undefined, "change": undefined, "votes" : "", "votepercent" : undefined};

		holdingArray.push(others);
    holdingArray.push(totals);
    holdingArray.push(stupidcsvextrarow);

    alterTable(area, holdingArray);
}


function alterTable(area, holdingarray){

  if (area == "all"){
    nationalVoteTotals = holdingarray;
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


// for browsers
var isFirefox = typeof InstallTrigger !== 'undefined';
var isIE = /*@cc_on!@*/false || !!document.documentMode



// for voteshare

function voteShare(){
	var value = partyVoteShare;

	var relevant_class = "." + value;
	var colour = $(relevant_class).css("background-color");
	var text_colour = $(relevant_class).css("color");

	if (value == "null"){
		$.each(seatData, function(seat){
			seatData[seat]["seat_info"]["current_colour"] = 1;
		})
		$(".map").remove()
		loadmap();
	}

	else {

		// resetFilter();

		var	max = 0;
		var min = 100;
		$.each(seatData, function(seat){
			if (value in seatData[seat]["party_info"]){
				var percentage = seatData[seat]["party_info"][value]["percent"];
				if (percentage > 0){
					if (percentage > max){
						max = percentage;
					}
					if (percentage < min){
						min = percentage;
					}
				}
			}
		})

		if (min > 20){
			min = 20;
		}

		if (max > 60){
			max = 60;
		}


		for (var i = 0; i < 651; i++){
			var id_vote_share = "#i" + i;

			d3.select(id_vote_share)
				.style("fill", colour)
				.attr("opacity", function(d) {
					var seat_name = seatsFromIDs[id_vote_share]
					if (value in seatData[seat_name]["party_info"]){
						var vote_share = seatData[seat_name]["party_info"][value]["percent"]
					}
					else {
						var vote_share = 0
					}

					var vote_range = max - min;
					vote_share = vote_share - min;
					if (vote_share < 0) {
						vote_share = 0
					}

					seatData[seat_name]["seat_info"]["current_colour"] = vote_share / vote_range

					return vote_share / vote_range;

				})
		}
	}
	keyOnMap(value, max, min, colour, text_colour);
	colour = null;

}


function keyOnMap(value, max, min, colour, text_colour){

	var orig_color = colour;

	$("#keyonmap").html("");

	if (value == "null"){
		null
	}

	else {

		var vote_range = max - min;
		var gap = vote_range / 5;
		var opacities = {};

		for (var i = 0; i < 6; i++){
			var num = (min + gap * i);
			var opacity = (num - min) / vote_range;
			num = num.toFixed(1);

			if (num == 60.0){
				num = num + "+";
			}
			opacities[num] = opacity;
		}

		$.each(opacities, function(num){
			colour = colour.replace(")", "," + opacities[num] +  ")").replace("rgb", "rgba")

			$("#keyonmap").append("<div style=\" color:"
			+ text_colour + "; text-align: center; background-color: "
			+ colour + "\">"
			+ num + "%</div>");

			colour = orig_color;

		});
	}
}


function swingFromTo(){

	$("#keyonmap").html("");

	partyA = swingState[0];
	partyB = swingState[1];

	if (swingState[0] != "null" && swingState[1] != "null" && swingState[0] != swingState[1]){


		var	max = 0;

		$.each(seatData, function(seat){
			if (partyA in seatData[seat]["party_info"] && partyB in seatData[seat]["party_info"]){
				var percentage_changeA = seatData[seat]["party_info"][partyA]["change"];
				var percentage_changeB = seatData[seat]["party_info"][partyB]["change"];
				var swing = (percentage_changeB - percentage_changeA) / 2
				if (Math.abs(swing) > max){

					max = Math.abs(swing);
				}
			}
		})

		for (var i = 1; i < 651; i++){
			var id_vote_share = "#i" + i;

			var	seat_name = seatsFromIDs[id_vote_share];

			var swing;

			if (partyA in seatData[seat_name]["party_info"] && partyB in seatData[seat_name]["party_info"]){

				var percentage_changeA = seatData[seat_name]["party_info"][partyA]["change"];
				var percentage_changeB = seatData[seat_name]["party_info"][partyB]["change"];
				swing = (percentage_changeB - percentage_changeA) / 2;
			}

			else {
				swing = 0;
			}

			if (max == 0) {
				d3.select(id_vote_share)
					.style("fill", "none")
					.attr("opacity", 0);
			}

			else if (swing >= 0){
				d3.select(id_vote_share)
				.style("fill", "blue")
				.attr("opacity", function(d){
					seatData[seat_name]["seat_info"]["current_colour"] = Math.abs(swing) / max;
					return swing / max ;
				})
			}

			else {
				d3.select(id_vote_share)
				.style("fill", "red")
				.attr("opacity", function(d){

					seatData[seat_name]["seat_info"]["current_colour"] = Math.abs(swing) / max;
					return Math.abs(swing) / max ;
				})
			}
		}
		swingKeyOnMap(max);
	}

	else {
		$.each(seatData, function(seat){
			seatData[seat]["seat_info"]["current_colour"] = 1;
		})
		$(".map").remove()
		loadmap();
	}


}


function swingKeyOnMap(max){
	$("#keyonmap").html("");

	var gap = max / 5;
	var opacities = {};

	for (var i = 0; i < 6; i++){
		var num = (max - gap * i);

		var opacity = (num) / max;
		num = num.toFixed(1);

		opacities[num] = opacity;
	}


	$.each(opacities, function(num){
		$("#keyonmap").append("<div style=\"text-align: center; background-color: rgba(0, 0, 255, " + opacities[num] + "); color: white;\">+"
		+ num + "%</div>");
	})

	var opacities = {};

	for (var i = 1; i < 6; i++){
		var num = (gap * i);

		var opacity = (num) / max;
		num = num.toFixed(1);

		opacities[num] = opacity;
	}

	$.each(opacities, function(num){
		$("#keyonmap").append("<div style=\"text-align: center; background-color: rgba(255, 0, 0, " + opacities[num] + "); color: white;\">-"
		+ num + "%</div>");
	})


}

function voteShareChange(){
	var value = partyVoteShareChange;

	var relevant_class = "." + value;

	if (value == "null"){
		$.each(seatData, function(seat){
			seatData[seat]["seat_info"]["current_colour"] = 1;
		})
		$(".map").remove()
		loadmap();
	}

	else {

		// resetFilter();

		var	max = 0;
		var min = 0;
		$.each(seatData, function(seat){
			if (value in seatData[seat]["party_info"]){
				var percentage = seatData[seat]["party_info"][value]["change"];
				if (percentage > max){
					max = percentage;
					}
				if (percentage < min){
					min = percentage;
					}
				}
			})

		if (Math.abs(min) > max){
			max = Math.abs(min);
		}

		for (var i = 1; i < 651; i++){
			var id_vote_share = "#i" + i;

			var seat_name = seatsFromIDs[id_vote_share];

			var change;

			if (value in seatData[seat_name]["party_info"]){
				change = seatData[seat_name]["party_info"][value]["change"]
				}
			else  {
				change = 0;
			}

			if (change >= 0){
				d3.select(id_vote_share)
					.style("fill", "blue")
					.attr("opacity",function(d){
						seatData[seat_name]["seat_info"]["current_colour"] = Math.abs(change) / max;
						return Math.abs(change) / max;
					});
			}

			else {
				d3.select(id_vote_share)
					.style("fill", "red")
					.attr("opacity",function(d){
						seatData[seat_name]["seat_info"]["current_colour"] = Math.abs(change) / max;
						return Math.abs(change) / max;
					});
			}

		}
		swingKeyOnMap(max);
		colour = null;
	}

}
