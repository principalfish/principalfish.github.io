// empty arrays for various data
var pageSetting = "2015parliament";
var seatData = {}; //seatData contains all information display on page. filled on page load using getSeatInfo
var seatsAfterFilter = []; // for use with user inputs in filters - changing map opacity + generating seat list at end
var searchSeatData = []; // for use with search box
var seatNames = []; // for use with search box
var seatsFromIDs = {}; // for translating IDs to seats

var currentSeats = []; // for use flashing new se
var totalElectorate = 0;

var previousTotals = {

	"2010" : {
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
						},

	"2015" : {
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
						"yorkshireandthehumber": 2368363}
};

var regions = {
    "england"  : ["northeastengland", "northwestengland", "yorkshireandthehumber", "southeastengland", "southwestengland", "eastofengland",
                  "eastmidlands", "westmidlands", "london"],
    "scotland"  : ["scotland"],
    "wales" : ["wales"],
    "northernireland" : ["northernireland"]
};

var swingState = ["null", "null"];

var partyVoteShare = "null"
var partyVoteShareChange = "null"


parties = ["conservative", "labour", "libdems", "ukip", "snp", "plaidcymru", "green", "uu", "sdlp", "dup", "sinnfein", "alliance", "other", "others", "independent", "respect", "speaker"]

// for browsers
var isFirefox = typeof InstallTrigger !== 'undefined';
var isIE = /*@cc_on!@*/false || !!document.documentMode;

var seatTotalContainer = "#totalfilteredseats";
var seatListContainer = "#filteredlisttable";

var filterStates = [{party: "null"}, {gain:"null"}, {region: "null"}, {majoritylow : 0}, {majorityhigh : 100}]

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
		translate = [width / 2 - scale * x, height / 3 - scale * y];

	disableZoom();

	svg.transition()
			.duration(1500)
			.call(zoom.translate(translate).scale(scale).event)
			.each("end", enableZoom);


	$("#seat-information").show();
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
	if ($("#seat-information").is(":visible")){
		$("#seat-information").hide();
		}
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

	if(pageSetting != "2010parliament"){
		if (seatInfo["winning_party"] != seatInfo["incumbent"]) {
			$(gainNameElement).text("Gain from " + partylist[seatInfo["incumbent"]]);
	        $(gainFlairElement).addClass(seatInfo["incumbent"]);
	    }
		else {
			$(gainNameElement).text("No change")
	    }
		}
	else {
		$(gainNameElement).text("");
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

	var width = 250 - margin.left - margin.right;
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

		var to_add = "<tr><td><div class= \" party-flair " + filterdata[i].party + "\"></div><td style=\"max-width: 170px;\">" +
			seatData[d.properties.name]["party_info"][filterdata[i].party]["name"] + "</td><td>" + (parseFloat(filterdata[i].votes)).toFixed(2) +  "%</td>";

		if (pageSetting == "2010parliament"){
			to_add += "</tr>";
		}
		else {
			to_add += ("<td>" + plussign + vote_change +  "</td></tr>");
		}

		$("#information-chart table").append(to_add);
	})

	if (seatData[d.properties.name]["seat_info"]["byelection"] != null){
		$("#information-byelection").text("By-election since " + pageSetting.slice(0, 4));
	}
	else {
		$("#information-byelection").text("");
	}
}

// load + colour map at page load
function loadmap(){

	d3.json("data/map.json", function(error, uk) {
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


// when user selects region this removes old vote list and replaces with selection
// problem with cache of tablesorter not being cleared result in multiple tables being displayed
// just rewriting the html as fix
function displayVoteTotals(data) {

		$("#totalstable").remove()

		var table_to_add = "<table id=\"totalstable\" class=\"tablesorter\"><thead><tr><th>Party</th>" +
												"<th class=\"tablesorter-header\">Seats</th>";



		if (pageSetting == "2010parliament"){
			table_to_add += "<th class=\"tablesorter-header\">Votes</th><th class=\"tablesorter-header\">Vote %</th></tr></thead><tbody id=\"totalstableinfo\"></tbody><tfoot id=\"totalstablefoot\">" +
			"</tfoot></table>";
		}

		else {
			table_to_add += "<th class=\"tablesorter-header\">Change</th>" +
			"<th class=\"tablesorter-header\">Votes</th><th class=\"tablesorter-header\">Vote %</th>" +
			"	<th class=\"tablesorter-header\">% +/-</th></tr></thead><tbody id=\"totalstableinfo\"></tbody><tfoot id=\"totalstablefoot\">" +
			"</tfoot></table>";
		}

		$("#totalstablediv").append(table_to_add);


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

				var table_foot = "<tr style=\"text-align: center;\" class=\"" + data[i].code +"\"><td style=\"text-align: left;\">"
				+ partylist[data[i].code] + "</td><td style=\"text-align: right;\">"
					+ data[i].seats + "</td>";

				if (pageSetting == "2010parliament"){
					table_foot += "<td style=\"text-align: right;\">" + data[i].votes.toLocaleString() + "</td><td></td></tr>";
				}

				else {
					table_foot += "<td></td><td style=\"text-align: right;\">" + data[i].votes.toLocaleString() + "</td><td></td><td></td></tr>";
				}

				$("#totalstablefoot").append(table_foot);
				}

			else if (data[i].votes > 0){

				var table_row = "<tr><td><div class=\"party-flair " + data[i].code + "\"></div>"
				+ partylist[data[i].code] + "</td><td style=\"text-align: right;\">"
				+ data[i].seats + "</td>";

				if (pageSetting == "2010parliament"){
					table_row += "<td style=\"text-align: right;\">" + data[i].votes.toLocaleString() + "</td><td style=\"text-align: center;\">"
					+ data[i].votepercent.toFixed(2) + "</td></tr>";
				}

				else {
					table_row += "<td style=\"text-align: right;\">"
					+ plussign1 + data[i].change + "</td><td style=\"text-align: right;\">"
					+ data[i].votes.toLocaleString() + "</td><td style=\"text-align: center;\">"
					+ data[i].votepercent.toFixed(2) + "</td><td>"
					+ plussign2 + percentChange + "</td></tr>";
				}

				$("#totalstableinfo").append(table_row);
			}

		})

		$("#totalstable").tablesorter({
				sortInitialOrder: "desc",
	      headers: {
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
			oldturnout +=  previousTotals[pageSetting.slice(0,4)][areas[area]]
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

function filterMap(){

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
	$(seatTotalContainer).html(" ");
	$(seatListContainer).html(" ");

	}

// resets all filters and map to default state
function resetFilter(){

	filterStates[0].party = "null";
	filterStates[1].gain = "null";
	filterStates[2].region = "null";
	filterStates[3].majoritylow = 0;
	filterStates[4].majorityhigh = 100;

	var previous_opacity = 1;
	var current_colour = 1;

	swingState = ["null", "null"];

	partyVoteShare = "null"
	partyVoteShareChange = "null"

	filterMap();

	$("#votesharebypartyselect option:eq(0)").prop("selected", true);
	$("#votesharechangebypartyselect option:eq(0)").prop("selected", true);
	$("#swingfrom option:eq(0)").prop("selected", true);
	$("#swingto option:eq(0)").prop("selected", true);


	$("#dropdownparty option:eq(0)").prop("selected", true);
	$("#dropdowngains option:eq(0)").prop("selected", true);
	$("#dropdownregion option:eq(0)").prop("selected", true);
	$("#majority").get(0).reset();

	$(seatTotalContainer).html(" ");
	$(seatListContainer).html(" ");
}

// using seatsAfterFilter, generates list of filtered seats
function generateSeatList(){
			$("#totalfilteredseats").html(" ");g
			$("#filteredlisttable").html(" ");

			$(seatTotalContainer).html(" ");
			$(seatListContainer).html(" ");

			$(seatTotalContainer).append("<p>Total : " + seatsAfterFilter.length + "</p>");
			$.each(seatsAfterFilter, function(i){

			if (filterStates[1].gain == "gains" && filterStates[0].party != "null"){
							$(seatListContainer).append("<div onclick=\"zoomToClickedFilteredSeat(seatsAfterFilter[" + i + "])\">"
              + "<div class=\"party-flair " + seatData[seatsAfterFilter[i].properties.name]["seat_info"]["incumbent"] + "\"></div>"
              + seatsAfterFilter[i].properties.name
              + "</div>")
				}

			else{
          $(seatListContainer).append("<div onclick=\"zoomToClickedFilteredSeat(seatsAfterFilter[" + i + "])\">"
          + '<div class=\"party-flair ' + seatData[seatsAfterFilter[i].properties.name]["seat_info"]["winning_party"] + '\"></div>'
          + seatsAfterFilter[i].properties.name
          + "</div>");
				}
		});
	}

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

		if (previousnode != undefined){
			var last_seat = seatData[seatsFromIDs[previousnode]];
			if (value in last_seat["party_info"] ){
				previous_opacity = (last_seat["party_info"][value]["percent"] - min) / (max - min);
				}

			else {
				previous_opacity = 0;
				}
		}

		flashSeat(d3.select(previousnode), current, previous_opacity, current_colour, "dontflash");

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
		if (previousnode != undefined){
			var last_seat = seatData[seatsFromIDs[previousnode]];

			if (partyA in last_seat["party_info"] && partyB in last_seat["party_info"]){
				swing = (last_seat["party_info"][partyB]["change"] - last_seat["party_info"][partyA]["change"] )/ 2;

				previous_opacity = Math.abs(swing) / max;

			}
			else {
				previous_opacity = 0;
			}
		}

		flashSeat(d3.select(previousnode), current, previous_opacity, current_colour, "dontflash");

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
		if (previousnode != undefined){

			var last_seat = seatData[seatsFromIDs[previousnode]];
			if (value in last_seat["party_info"]){
				previous_opacity = (Math.abs(last_seat["party_info"][value]["change"]) / max);
			}
			else {
				previous_opacity = 0;
			}
		}

		flashSeat(d3.select(previousnode), current, previous_opacity, current_colour, "dontflash");

		swingKeyOnMap(max);
		colour = null;
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

function loadTheMap(url){
	seatData = {};
	seatsAfterFilter = [];
	totalElectorate = 0;
	$(".map").remove();
	$(document).ready(function(){ getData("data/" + url + "/info.json").done(getSeatInfo)});
}

loadTheMap("2015parliament");

var previousSetting = "2015parliament"

function alterTheUI(setting){

	pageSetting = setting;

	resetFilter();
	$("#selectareatotals option:eq(0)").prop("selected", true);

	var alterClass = "#nav" + previousSetting;
	var alterSelected = "#nav" + pageSetting;


	$(alterClass).attr("class", "notactive");
	$(alterSelected).attr("class", "currentpage");


	if (setting == "2015parliament") {
		$("title").text("UK Election Maps - 2015 Parliament");
		$("#headertitle").text("2015 Parliament");
		$("#dropdowngainslabel").show();
		$("#dropdowngains").show();
		$("#swingfromto").show();
		$("#votesharechangebyparty").show();

	}

	if (setting == "2010parliament"){

		$("title").text("UK Election Maps - 2010 Parliament");
		$("#headertitle").text("2010 Parliament");

		$("#dropdowngainslabel").hide();
		$("#dropdowngains").hide();
		$("#swingfromto").hide();
		$("#votesharechangebyparty").hide();
	}

	if (setting == "2015projection"){

		$("title").text("UK Election Maps - 2015 Projection");
		$("#headertitle").text("2015 Projection");
	
		$("#dropdowngainslabel").show();
		$("#dropdowngains").show();
		$("#swingfromto").show();
		$("#votesharechangebyparty").show();
	}

	previousSetting = setting;


}
