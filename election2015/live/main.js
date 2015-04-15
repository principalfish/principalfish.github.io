// FOR index and nowcast

//add to d3js prototype
// brings elements to top when (for use when clicking on seat)
d3.selection.prototype.moveToFront = function() {
							return this.each(function(){
							this.parentNode.appendChild(this);
						});
					};

// current state of user input filters
var filterStates = [{party: "null"}, {gain:"null"}, {region: "null"}, {majoritylow : 0}, {majorityhigh : 100}]

// empty arrays for various data
var seatsAfterFilter = []; // for use with user inputs in filters - changing map opacity + generating seat list at end
var searchSeatData = []; // for use with search box
var seatNames = []; // for use with search box

// control flow for analysing user filter inputs
function filterMap(){

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


		});
	}

// resets all filters and map to default state
function resetFilter(){

	filterStates[0].party = "null";
	filterStates[1].gain = "null";
	filterStates[2].region = "null";
	filterStates[3].majoritylow = 0;
	filterStates[4].majorityhigh = 100;

	filterMap();

	$("#dropdownparty option:eq(0)").prop("selected", true);
	$("#dropdowngains option:eq(0)").prop("selected", true);
	$("#dropdownregion option:eq(0)").prop("selected", true);
	$("#majority").get(0).reset()

	$("#totalfilteredseats").html(" ");
	$("#filteredlisttable").html(" ");
}

// using seatsAfterFilter, generates list of filtered seats
function generateSeatList(){
			$("#totalfilteredseats").html(" ");g
			$("#filteredlisttable").html(" ");

			$("#totalfilteredseats").append("<p>Total : " + seatsAfterFilter.length + "</p>");
			$.each(seatsAfterFilter, function(i){

				if (filterStates[1].gain == "gains" && filterStates[0].party != "null")
				$("#filteredlisttable").append("<tr class=" + seatData[seatsAfterFilter[i].properties.name]["seat_info"]["incumbent"] +
				"><td onclick=\"zoomToClickedFilteredSeat(seatsAfterFilter[" + i + "])\">" + seatsAfterFilter[i].properties.name + "</td></tr>")


				else
					$("#filteredlisttable").append("<tr class=" + seatData[seatsAfterFilter[i].properties.name]["seat_info"]["winning_party"] +
					"><td onclick=\"zoomToClickedFilteredSeat(seatsAfterFilter[" + i + "])\">" + seatsAfterFilter[i].properties.name + "</td></tr>")

			});
		}

// close to duplicate of clicked() due to slightly difference in data type used. fix at some point. this one is for generate seat list and seat search box
function zoomToClickedFilteredSeat(d){
	var id = "#i" + seatData[d.properties.name]["seat_info"]["id"];
	//rewrite at some point

	previous = d3.select(previousnode)
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

// scripts for zoom/pan/clicked


// close to duplicate of zoomtoclickedfilteredseat() due to slightly difference in data type used. fix at some point. this one is for normal map clicking
function clicked(d) {

	previous = d3.select(previousnode)
	current = d3.select(this);

	repeat();

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
	previousnode = this;
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
var oldclass = null;

// fills out table of info at top of #right
function seatinfo(d){

	$("#information").removeClass(oldclass);
	$("#information").addClass(seatData[d.properties.name]["seat_info"]["winning_party"])
	$("#information-seatname").html("<td>Seat</td><td style=\"width:360px\"> " + d.properties.name +
	"</td><td id=\"rightcolumninfotable\">" + regionlist[seatData[d.properties.name]["seat_info"]["area"]] + "</td>");
	$("#information-party").html("<td>Party</td><td>" + partylist[seatData[d.properties.name]["seat_info"]["winning_party"]] + "</td>");
	if (seatData[d.properties.name]["seat_info"]["winning_party"] != seatData[d.properties.name]["seat_info"]["incumbent"])
		$("#information-gain").html("<td>Gain from</td><td><span id=\"information-gain-span\"class=\"" +
		seatData[d.properties.name]["seat_info"]["incumbent"] + "\">" + partylist[seatData[d.properties.name]["seat_info"]["incumbent"]] + "</span></td>")
	else
		$("#information-gain").html("<td>No change </td>")
	$("#information-pie").html(piechart(d));

	oldclass = seatData[d.properties.name]["seat_info"]["winning_party"];
}

// d3 make pie chart of vote counts ni selected seat
function piechart(d){

	$("#information-pie").empty();
	$("#information-chart").empty();

	$("#information-chart").html("<p></p>");

	var data = [];

	relevant_party_info = seatData[d.properties.name]["party_info"]
	$.each(relevant_party_info, function(d){
		votes = relevant_party_info[d]["vote_percentage"]
		data.push({party: d, votes: votes})
	})

	var filterdata = [];

	$.each(data, function(i){

		if (data[i].votes > 0 && data[i].party != "other")
			filterdata.push(data[i]);
	});

	filterdata.sort(function(a, b){
			return b.votes - a.votes ;
	});

	var keys = Object.keys(relevant_party_info)


	if (keys.indexOf("other") != -1){
		filterdata.push({party: "other", votes: relevant_party_info["other"]["vote_percentage"]})
	}


	var width = 250,
		height = 250;
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

	// creates table with vote counts/percentages
	$.each(filterdata, function(i){
		$("#information-chart").append("<tr class=" + filterdata[i].party + " style=\"font-weight: bold;\"><td>" +
			partylist[filterdata[i].party] + "</td><td>" + (parseFloat(filterdata[i].votes)).toFixed(2) +  "%</td></tr>")
	})
}

// load + colour map at page load
function loadmap(){
	d3.json("/election2015/data/projection.json", function(error, uk) {
		if (error) return console.error(error);

		g.selectAll(".map")
			.data(topojson.feature(uk, uk.objects.projection).features)
			.enter().append("path")
			.attr("class", function(d) {
			
				if (seatData[d.properties.name]["seat_info"]["winning_party"] != undefined){
					return "map " + seatData[d.properties.name]["seat_info"]["winning_party"] ;}
				else{
					return "null";
				}

				})
			.attr("opacity", 1)
			.attr("id", function(d) { return "i" + seatData[d.properties.name]["seat_info"]["id"]})
			.attr("d", path)
			.on("click", clicked)
			.append("svg:title")
				.text(function(d) { return d.properties.name})
			.each(function(d){
				seatsAfterFilter.push(d)
				searchSeatData.push(d)
				seatNames.push(d.properties.name);
			});

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
		$("#totalstable").remove()
		$("#totals").append("<table id=\"totalstable\" class=\"tablesorter\"><thead><tr><th>Party</th>" +
												"<th class=\"tablesorter-header\">Seats</th><th class=\"tablesorter-header\">Change</th>" +
												"<th class=\"tablesorter-header\">Vote %</th><th class=\"tablesorter-header\">+/-</th>" +
												"</tr></thead><tbody id=\"totalstableinfo\"></tbody><tfoot id=\"totalstablefoot\">" +
												"</tfoot></table>")

		var plussign1, plussign2;

		$.each(data, function(i){

				if (data[i].change > 0)
					plussign1 = "+";
				else
					plussign1 = "";

				if (data[i].votepercentchange > 0)
					plussign2 = "+";
				else
					plussign2 = "";

			if (i == data.length -1)
					null

			else if (i == data.length -2)

				$("#totalstablefoot").append("<tr class=\"" + data[i].code +"\"><td>" + partylist[data[i].code] + "</td><td>"
					+ data[i].seats + "</td><td>" + data[i].change + "</td><td>" + (data[i].votepercent).toFixed(2) +
					"</td><td>" + (data[i].votepercentchange) + "</td></tr>");
			else
				if (data[i].votepercent > 0)
					$("#totalstableinfo").append("<tr class=\"" + data[i].code +"\"><td>" + partylist[data[i].code] + "</td><td>"
						+ data[i].seats + "</td><td>"  + plussign1 + data[i].change + "</td><td>" + (data[i].votepercent).toFixed(2) +
						"</td><td>"  + plussign2 + (data[i].votepercentchange).toFixed(2) + "</td></tr>");
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

// fills seatData, loads map afterwards
function getSeatInfo(data){
	d3.json("info.json", function(error, seats) {
		if (error) return console.error(error);

		$.each(seats, function(seat){

			seatData[seat] = seats[seat]

		});
	})

	loadmap()
	//getVoteTotals()

	//
}

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

// function to populate above arrays
// regions_in_uk = ["london", "southeastengland", "southwestengland", "westmidlands", "northwestengland", "northeastengland",
// 								"yorkshireandthehumber", "eastmidlands", "eastofengland", "scotland", "wales", "northernireland"]

//initiate data accrual + map load
getSeatInfo();


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