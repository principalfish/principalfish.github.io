// empty arrays for various data
var seatData = {}; //seatData contains all information display on page. filled on page load using getSeatInfo
var seatsAfterFilter = []; // for use with user inputs in filters - changing map opacity + generating seat list at end
var searchSeatData = []; // for use with search box
var seatNames = []; // for use with search box
var seatsFromIDs = {}; // for translating IDs to seats

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
};

var regions = {
    "england"  : ["northeastengland", "northwestengland", "yorkshireandthehumber", "southeastengland", "southwestengland", "eastofengland",
                  "eastmidlands", "westmidlands", "london"],
    "scotland"  : ["scotland"],
    "wales" : ["wales"],
    "northernireland" : ["northernireland"]
};

parties = ["conservative", "labour", "libdems", "ukip", "snp", "plaidcymru", "green", "uu", "sdlp", "dup", "sinnfein", "alliance", "other", "others"]

// for browsers
var isFirefox = typeof InstallTrigger !== 'undefined';
var isIE = /*@cc_on!@*/false || !!document.documentMode;

var previous_opacity ;
var current_colour;

function zoomToClickedFilteredSeat(d){
	var id = "#i" + d.properties.info_id;

	if (previousnode != undefined){
		var previous_seat = seatsFromIDs[previousnode];
		previous_opacity = seatData[previous_seat]["seat_info"]["current_colour"];
		}

	if (previous_opacity == undefined){
		previous_opacity = 1;
	}

	var current_seat = seatsFromIDs[id];
	current_colour = seatData[current_seat]["seat_info"]["current_colour"];

	if (current_colour == undefined){
		current_colour = 1;
	}

	previous = d3.select(previousnode);
	current = d3.select(id);

	flashSeat(previous, current, previous_opacity, current_colour);

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

function flashSeat(previous, current, previous_opacity, current_colour, optional){

	if (optional == undefined){
		repeat();
	}
	previous.transition()
		.attr("opacity", previous_opacity);
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

// fills out table of info at top of #right
var oldPartyClass = null;
var oldIncumbentClass = null;

var partyNameElement = "#information-party .party-name";
var partyFlairElement = "#information-party .party-flair";
var gainNameElement = "#information-gain .party-name";
var gainFlairElement = "#information-gain .party-flair";

function seatinfo(d){

	var seatInfo = seatData[d.properties.name]["seat_info"];
	$(partyFlairElement).removeClass(oldPartyClass);
    $(gainFlairElement).removeClass(oldIncumbentClass);
	$("#information-region").text("Region: " + regionlist[seatInfo["area"]]);
    $("#information-seatname").text("Seat: " + d.properties.name);
	$(partyNameElement).text("Party: " + partylist[seatInfo["winning_party"]]);
    $(partyFlairElement).addClass(seatInfo["winning_party"]);
	if (seatInfo["winning_party"] != seatInfo["incumbent"]) {
		$(gainNameElement).text("Gain from " + partylist[seatInfo["incumbent"]]);
        $(gainFlairElement).addClass(seatInfo["incumbent"]);
    }
	else {
		$(gainNameElement).text("No change")
    }
	$("#information-majority").text("Majority: " + seatInfo["maj_percent"]
	+ " % = " + seatInfo["maj"]);

	var seatTurnout = (100 * seatInfo["turnout"] / seatInfo["electorate"]).toFixed(2) + "%";

	$("#information-turnout").text("Turnout : " + seatTurnout )

	$("#information-pie").html(piechart(d));

	oldPartyClass = seatInfo["winning_party"];
    oldIncumbentClass = seatInfo["incumbent"];
}

function piechart(d){
	$("#information-pie").empty();
	$("#information-chart").empty();
	$("#information-chart").html("<table></table>");

	var data = [];
	var relevant_party_info = seatData[d.properties.name]["party_info"];

	$.each(relevant_party_info, function(d){
		votes = relevant_party_info[d]["percent"];
		if (votes > 0){
			data.push({party: d, votes: votes, vote_change: relevant_party_info[d]["change"]});
		}
	})

	var filterdata = [];

	$.each(data, function(i){
		if (data[i].votes > 0 && data[i].party != "others")
			filterdata.push(data[i]);
	});

	filterdata.sort(function(a, b){
			return b.votes - a.votes;
	});

	var keys = Object.keys(relevant_party_info);

	if (keys.indexOf("others") != -1 && relevant_party_info["others"]["percent"] > 0){
		filterdata.push({party: "others", votes: relevant_party_info["others"]["percent"], vote_change: relevant_party_info["others"]["change"]});
	}

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

		$("#information-chart table").append("<tr><td><div class= \" party-flair " + filterdata[i].party + "\"></div><td style=\"max-width: 170px;\">" +
			seatData[d.properties.name]["party_info"][filterdata[i].party]["name"] + "</td><td>" + (parseFloat(filterdata[i].votes)).toFixed(2) +  "%</td><td>"
			+ plussign + vote_change +  "</td></tr>");
	})
}

// load + colour map at page load
function loadmap(){

	d3.json("data/2015parliament/livemap.json", function(error, uk) {
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



// add number with comma parser to tablesorter.
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

		$("#totalstable").remove()
		$("#totalstablediv").append("<table id=\"totalstable\" class=\"tablesorter\"><thead><tr><th>Party</th>" +
												"<th class=\"tablesorter-header\">Seats</th><th class=\"tablesorter-header\">Change</th>" +
												"<th class=\"tablesorter-header\">Votes</th><th class=\"tablesorter-header\">Vote %</th>" +
												"	<th class=\"tablesorter-header\">% +/-</th></tr></thead><tbody id=\"totalstableinfo\"></tbody><tfoot id=\"totalstablefoot\">" +
												"</tfoot></table>")


		var percentChange;
		var plussign1, plussign2;

		$.each(data, function(i){

				if (data[i].change > 0)
					plussign1 = "+";
				else
					plussign1 = "";

				if (data[i].percentchange > 0)
					plussign2 = "+";
				else
					plussign2 = "";

			if (data[i].percentchange == undefined){
			 	percentChange = "";
			}
			else {
				percentChange = data[i].percentchange.toFixed(2);
			}

			if (i == data.length -1){

				$("#totalstablefoot").append("<tr style=\"text-align: center;\" class=\"" + data[i].code +"\"><td style=\"text-align: left;\">"
				+ partylist[data[i].code] + "</td><td style=\"text-align: right;\">"
					+ data[i].seats + "</td><td></td><td style=\"text-align: right;\">"
					+ data[i].votes.toLocaleString() + "</td><td></td><td></td></tr>");
				}
			else if (data[i].votes > 0){

				$("#totalstableinfo").append("<tr><td><div class=\"party-flair " + data[i].code + "\"></div>"
				+ partylist[data[i].code] + "</td><td style=\"text-align: right;\">"
				+ data[i].seats + "</td><td style=\"text-align: right;\">"
				+ plussign1 + data[i].change + "</td><td style=\"text-align: right;\">"
				+ data[i].votes.toLocaleString() + "</td><td style=\"text-align: center;\">"
				+ data[i].votepercent + "</td><td>"
				+ plussign2 + percentChange + "</td></tr>");
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

		holdingArray.push(others);
    holdingArray.push(totals);


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
			oldElectorate += data[seat]["seat_info"]["old_electorate"];
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

	var totalTurnout = 100 * nationalVoteTotals[nationalVoteTotals.length - 1].votes / totalElectorate ;

	if (isNaN(totalTurnout)){
		totalTurnout = 100;
	}


	totalTurnout = "Turnout : " + String(totalTurnout.toFixed(2)) + "%"
	document.getElementById("totalturnout").innerHTML = totalTurnout
}

// call the function
$(document).ready(function(){ getData("data/2015parliament/info.json").done(getSeatInfo)});
