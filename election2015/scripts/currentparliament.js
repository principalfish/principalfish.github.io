//add to d3js prototype
d3.selection.prototype.moveToFront = function() {
							return this.each(function(){
							this.parentNode.appendChild(this);
						});
					}; 	

// far left filter scripts
var filterStates = [{party: "null"}, {majoritylow: NaN}, {majorityhigh: NaN}, {region: "null"}]

var seatsAfterFilter = [];
var searchSeatData = [];
var seatNames = [];


	

function filterMap(){

	var	party  = filterStates[0].party;
	var majoritylow = filterStates[1].majoritylow;
	var majorityhigh = filterStates[2].majorityhigh;
	var region = filterStates[3].region;
	
	seatsAfterFilter = [];
		
	d3.json("map.json", function(uk){
		
		if (party == "null" && majority == "null" && majoritylow == NaN && majorityhigh == NaN)
			g.selectAll(".map")
				.attr("style", "opacity:1")
						
		else
		
			if (isNaN(majoritylow))
				majoritylow = 0 
			if (isNaN(majorityhigh))
				majorityhigh = 1000000
				
			g.selectAll(".map")
				.attr("id", "filtertime")
				
			if (party == "null")
				g.selectAll("#filtertime")
					.attr("id", "partyfiltered")
			else
				g.selectAll("#filtertime")					
					.attr("style", function(d){
						if (party != d.properties.incumbent)
							return "opacity: 0.1";
						})					
					.attr("id", function(d){
						if (party == d.properties.incumbent)
							return "partyfiltered"	
						});
											
				
			
			g.selectAll("#partyfiltered")				
				.attr("style", function(d) {
					if (d.properties.info_majorityvotes < majoritylow || d.properties.info_majorityvotes > majorityhigh )
						return "opacity: 0.1"
					})
				.attr("id", function(d) {
					if (d.properties.info_majorityvotes >= majoritylow && d.properties.info_majorityvotes <= majorityhigh )
						return "majorityfiltered";
					});
			
		
			
			if (region == "null")
				g.selectAll("#majorityfiltered")
					.each(function(d){
							seatsAfterFilter.push(d)					
												
						})
			else
				g.selectAll("#majorityfiltered")	
					.attr("style", function(d){																		
						if (region != d.properties.region)								
							return "opacity: 0.1";	
					})
					.attr("id", function(d){																		
						if (region == d.properties.region)			
							seatsAfterFilter.push(d);												
					});	
					
			g.selectAll(".map")
				.attr("id", function(d) {
					return "i" + d.properties.info_id
				});
			$("#totalfilteredseats").html(" ");
			$("#filteredlisttable").html(" ");
			
	
		});
	}

function resetFilter(){

	filterStates[0].party = "null"
	filterStates[1].majoritylow = NaN
	filterStates[2].majorityhigh = NaN
	filterStates[3].region = "null"							
	filterMap();
	
	$("#dropdownparty option:eq(0)").prop("selected", true);
	$("#majority").get(0).reset()
	$("#dropdownregion option:eq(0)").prop("selected", true);

	$("#totalfilteredseats").html(" ");
	$("#filteredlisttable").html(" ");
}

function generateSeatList(){			
			$("#totalfilteredseats").html(" ");
			$("#filteredlisttable").html(" ");
			
			$("#totalfilteredseats").append("<p>Total : " + seatsAfterFilter.length + "</p>");
			$.each(seatsAfterFilter, function(i){
				$("#filteredlisttable").append("<tr class=" + seatsAfterFilter[i].properties.incumbent + 
				"><td onclick=\"zoomToClickedFilteredSeat(seatsAfterFilter[" + i + "])\">" + seatsAfterFilter[i].properties.name + "</td></tr>")
				
			});
				
			
		}	
		
function zoomToClickedFilteredSeat(d){
	var id = "#i" + d.properties.info_id;	
	//rewrite at some point
	
	previous = d3.select(previousnode)
	current = d3.select(id);
	
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
	previousnode = id;
	}




// scripts for zoom/pan/clicked 


var blah;
function clicked(d) {
	blah = d;
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

function disableZoom(){
	svg.on("mousedown.zoom", null);
	svg.on("mousemove.zoom", null);
	svg.on("dblclick.zoom", null);
	svg.on("touchstart.zoom", null);
	svg.on("wheel.zoom", null);
	svg.on("mousewheel.zoom", null);
	svg.on("MozMousePixelScroll.zoom", null);
}

function enableZoom(){
	svg.call(zoom);
}

function reset() {
	disableZoom();
	svg.transition()
		.duration(1500)
		.call(zoom.translate([0, 0]).scale(1).event)
		.each("end", enableZoom);
		
}

function zoomed() {	
	g.style("stroke-width", 1.5 / d3.event.scale + "px");
	g.attr("transform", "translate(" + d3.event.translate + ")scale(" + d3.event.scale + ")");
	
}

function stopped() {
	if (d3.event.defaultPrevented) d3.event.stopPropagation();
}

// seatinfo on right

var oldclass = null;

function seatinfo(d){
	
	$("#information").removeClass(oldclass);		
	$("#information").addClass(d.properties.incumbent)			
	$("#information-seatname").html("<td>Seat</td><td style=\"width:380px\"> " + d.properties.name + 
		"<span id =\"information-byelection\"></span></td><td id=\"rightcolumninfotable\">" + regionlist[d.properties.region] + "</td>");
	if (d.properties.info_bielection == "yes")
		$("#information-byelection").html("*");					
	$("#information-party").html("<td>Party</td><td>" + partylist[d.properties.incumbent] + "</td>");
	$("#information-mp").html("<td>MP</td><td>" + d.properties.info_mpfirstname + " " + d.properties.info_mplastname + "</td>");
	$("#information-majority").html("<td>Majority</td><td> " + d.properties.info_majorityvotes  + "  =  " + (d.properties.info_majoritypercent).toFixed(2) + "%</td>");																	
	$("#information-electorate").html("<td>Turnout </td><td>" + d.properties.info_votescast + " = " + (d.properties.info_turnout).toFixed(2) + 
		"%</td><td id=\"rightcolumninfotable\">Electorate: " + d.properties.info_electorate + "</td>");	
	$("#information-pie").html(piechart(d));
	
	oldclass = d.properties.incumbent;
}



function piechart(d){
	
	$("#information-pie").empty();
	$("#information-chart").empty();
	
	$("#information-chart").html("<p></p>");

	var data = [];
	data.push({ party: "labour", votes: d.properties.info_LAB});
	data.push({ party: "conservative", votes: d.properties.info_CON});
	data.push({ party: "libdems", votes: d.properties.info_LIB});
	data.push({ party: "ukip", votes: d.properties.info_UKIP});
	data.push({ party: "snp", votes: d.properties.info_SNP});
	data.push({ party: "plaidcymru", votes: d.properties.info_PC});
	data.push({ party: "green", votes: d.properties.info_GRN});
	data.push({ party: "dup", votes: d.properties.info_DUP});
	data.push({ party: "sdlp", votes: d.properties.info_SDLP});
	data.push({ party: "uu", votes: d.properties.info_UU});
	data.push({ party: "sinnfein", votes: d.properties.info_SF});
	data.push({ party: "alliance", votes: d.properties.info_ALL});
	data.push({ party: "other1", votes: d.properties.info_OTH1});
	data.push({ party: "other2", votes: d.properties.info_OTH2});
	

	var filterdata = [];
	
	
	$.each(data, function(i){	
		
		if (data[i].votes > 0 && data[i].party != "other2")			
			filterdata.push(data[i]);
	});
	

	
	filterdata.sort(function(a, b){						
			return b.votes - a.votes ; 				
	});
	
	if (d.properties.info_OTH2 > 0)
		filterdata.push({party: "other2", votes: d.properties.info_OTH2})
	
	
	// better way of doing this?
	
	
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
			partylist[filterdata[i].party] + "</td><td>" + filterdata[i].votes + 
			"</td><td>" + (100 * filterdata[i].votes/d.properties.info_votescast).toFixed(2) +  "%</td></tr>")
	})
	
	
	
		
}


	
// bottom of right - vote chart 


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

function doStuff(data) {		
		$.each(data, function(i){
			if (i == data.length -1)
				$("#totalstable").append("<tfoot><tr class=\"" + data[i].code +"\"><td>" + data[i].party + "</td><td>" 
				+ data[i].seats + "</td><td>" + (data[i].votes).toLocaleString() + "</td><td>" + data[i].votesperseat.toLocaleString() + "</td><td>" 
				+ (data[i].votepercent).toFixed(2) + "</td></tr></tfoot>")
			else
				$("#totalstable").append("<tr class=\"" + data[i].code +"\"><td>" + data[i].party + "</td><td>" 
				+ data[i].seats + "</td><td>" + (data[i].votes).toLocaleString() + "</td><td>" + data[i].votesperseat.toLocaleString() + "</td><td>" 
				+ (data[i].votepercent).toFixed(2) + "</td></tr>")
	
		})
		
	 $(document).ready(function() { 
		$("table").tablesorter({
			headers: {
				2: {
					sorter: "numberWithComma"
				},
				3: {
					sorter: "numberWithComma"
				},
				0: { 
					sorter: false
				},
				4: { 
					sorter: false
				}
				
			},
			sortList:[[2,1], [1,0], [3,1]]
			
		}); 
	});
};

function parseData(url, callBack) {
	Papa.parse(url, {
		download: true,
		header: true,								
		dynamicTyping: true,
		complete: function(results) {
			callBack(results.data);
		}
	});
}

parseData("votetotals.csv", doStuff);
	
	
// autocomplete 

function searchSeats(value){
	$.each(searchSeatData, function(i){
		if (searchSeatData[i].properties.name == value)
			zoomToClickedFilteredSeat(searchSeatData[i])
	});
};

$(function()
{

	$("#searchseats").autocomplete({	
		source: seatNames,
		select: function(event, ui){
			searchSeats(ui.item.label);
		}
	
	});
		
});