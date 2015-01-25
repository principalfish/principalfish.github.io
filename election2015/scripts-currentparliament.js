//add to d3js prototype
d3.selection.prototype.moveToFront = function() {
							return this.each(function(){
							this.parentNode.appendChild(this);
						});
					}; 	

// far left filter scripts
filterStates = [{party: "null"}, {majority: "null"}, {region: "null"}, {turnout: "null"}]

function filterMap(){

	var	party  = filterStates[0].party;
	var majority = filterStates[1].majority;
	var region = filterStates[2].region;
	var turnout =  filterStates[3].turnout;
	
						
	d3.json("map.json", function(uk){
		
		if (party == "null" && majority == "null" && region == "null" && turnout == "null")
			g.selectAll(".map")
				.attr("style", "opacity:1")
			
		else
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
											
				
			if (majority == "null")
				g.selectAll("#partyfiltered")
					.attr("id", "majorityfiltered")
			else	
				g.selectAll("#partyfiltered")
					.attr("style", function(d){									
						if (majority == "marginal" && d.properties.info_majorityvotes >= 1000)								
							return "opacity: 0.1";
							
						if (majority == "close" && (d.properties.info_majorityvotes < 1000|| d.properties.info_majorityvotes >= 3000))							
							return "opacity: 0.1";
							
						if (majority == "safeish" && (d.properties.info_majorityvotes < 3000 || d.properties.info_majorityvotes >= 7000))								
							return "opacity: 0.1";
							
						if (majority == "safe" && d.properties.info_majorityvotes < 7000)								
							return "opacity: 0.1";								
						})								
					.attr("id", function(d){									
						if (majority == "marginal" && d.properties.info_majorityvotes < 1000)								
							return "majorityfiltered"
							
						if (majority == "close" && (d.properties.info_majorityvotes >= 1000 && d.properties.info_majorityvotes < 3000))							
							return "majorityfiltered";
							
						if (majority == "safeish" && (d.properties.info_majorityvotes >= 3000 && d.properties.info_majorityvotes < 7000))								
							return "majorityfiltered";
							
						if (majority == "safe" && d.properties.info_majorityvotes >= 7000)								
							return "majorityfiltered";								
						});
					
			if (region == "null")
				g.selectAll("#majorityfiltered")
					.attr("id", "regionfiltered")
			else
				g.selectAll("#majorityfiltered")	
					.attr("style", function(d){																		
						if (region != d.properties.region)								
							return "opacity: 0.1";	
					})
					.attr("id", function(d){																		
						if (region == d.properties.region)								
							return "regionfiltered";		
					});	
					
			if (region == "null")
				g.selectAll("#majorityfiltered")
					.attr("id", "regionfiltered")
			else
				g.selectAll("#majorityfiltered")	
					.attr("style", function(d){																		
						if (region != d.properties.region)								
							return "opacity: 0.1";	
					})
					.attr("id", function(d){																		
						if (region == d.properties.region)								
							return "regionfiltered";		
					});	
			
			
			if (turnout == "null")
				g.selectAll("#regionfiltered")
					.attr("id", "null")
			else
				g.selectAll("#regionfiltered")
					.attr("style", function(d){	
						if (turnout == "low" && d.properties.info_turnout >= 55)								
							return "opacity: 0.1";
							
						if (turnout == "average" && (d.properties.info_turnout < 55|| d.properties.info_turnout >= 65))							
							return "opacity: 0.1";
							
						if (turnout == "high" && (d.properties.info_turnout < 65 || d.properties.info_turnout >= 75))								
							return "opacity: 0.1";
							
						if (turnout == "extreme" && d.properties.info_turnout < 75)								
							return "opacity: 0.1";
				})
					.attr("id", "null");
		});
	}

function resetFilter(){

	filterStates[0].party = "null"
	filterStates[1].majority = "null"
	filterStates[2].region = "null"
	filterStates[3].turnout = "null"						
	filterMap();
	
	$("#dropdownparty option:eq(0)").prop("selected", true);
	$("#dropdownmajority option:eq(0)").prop("selected", true);
	$("#dropdownregion option:eq(0)").prop("selected", true);
	$("#dropdownturnout option:eq(0)").prop("selected", true);
	
	
}



// scripts for zoom/pan/clicked 

function clicked(d) {

		d3.select(previousnode)
			.style("stroke", "darkgrey")
			.style("stroke-width", 0.1);
			
		
		d3.select(this)
			.moveToFront()
			.transition()
			.style("stroke", "black")
			.style("stroke-width", 0.5);
	
			
		
		
		
		if (active.node() === this) return reset();
		active.classed("active", false);
		active = d3.select(this).classed("active", true);

		var bounds = path.bounds(d),
			dx = bounds[1][0] - bounds[0][0],
			dy = bounds[1][1] - bounds[0][1],
			x = (bounds[0][0] + bounds[1][0]) / 2,
			y = (bounds[0][1] + bounds[1][1]) / 2,
			scale = .25 / Math.max(dx / width, dy / height),
			translate = [width / 2 - scale * x, height / 2 - scale * y];

		svg.transition()
			.duration(1500)
			.call(zoom.translate(translate).scale(scale).event);
		
		
		seatinfo(d);
		previousnode = this;
	
	}

	function reset() {
		active.classed("active", false);
		active = d3.select(null);

		svg.transition()
			.duration(1500)
			.call(zoom.translate([0, 0]).scale(1).event);
			
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
	$("#information").addClass(d.properties.incumbent).css("opacity", 0.8);				
	$("#information-seatname").html("<td>Seat</td><td style=\"width:380px\"> " + d.properties.name + "<span id =\"information-byelection\"></span></td><td id=\"rightcolumninfotable\">" + regionlist[d.properties.region] + "</td>");
	if (d.properties.info_bielection == "yes")
		$("#information-byelection").html("*");					
	$("#information-party").html("<td>Party</td><td>" + partylist[d.properties.incumbent] + "</td>");
	$("#information-mp").html("<td>MP</td><td>" + d.properties.info_mpfirstname + " " + d.properties.info_mplastname + "</td>");
	$("#information-majority").html("<td>Majority</td><td> " + d.properties.info_majorityvotes  + "  =  " + d.properties.info_majoritypercent + "%</td>");																	
	$("#information-electorate").html("<td>Turnout </td><td>" + d.properties.info_votescast + " = " + d.properties.info_turnout + "%</td><td id=\"rightcolumninfotable\">Electorate: " + d.properties.info_electorate + "</td>");	
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
	data.push({ party: "other1", votes: d.properties.info_OTH1});
	data.push({ party: "other2", votes: d.properties.info_OTH2});
	

	var filterdata = [];
	
	
	$.each(data, function(i){
		if (data[i].votes > 0)
			filterdata.push(data[i]);
	});
	

	
	filterdata.sort(function(a, b){						
			return b.votes - a.votes ; 				
	});
	
	
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

function doStuff(data) {	

	$.each(data, function(i){
		$("#totals table").append("<tr class=\"" + data[i].code +"\"><td>" + data[i].party + "</td><td>" + data[i].seats + "</td><td>"
		+ data[i].net + "</td><td>" + (data[i].votes).toLocaleString() + "</td><td>" + data[i].votepercent +"</td></tr>")
	})
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
				
