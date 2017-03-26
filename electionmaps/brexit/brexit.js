var choro = {

  keyOnMap: function(){

    $("#keyonmap").show();

    var increment = 2

    // add key twice including negative for vote share

    $("#keyonmap").append("<div class='leave'>Leave</div>")
    for (var i=5; i > 0; i--){
      var background = "rgba(0, 0, 255, " + i * 0.2 + ")";

      var toAdd = "<div class='choro-minus' style='background-color: " + background + "'>"
                  +  (50 + i * increment).toFixed(1) + "%";

      if (i == 5){
        toAdd += "+";
      }

      toAdd += "</div>";

      $("#keyonmap").append(toAdd);
    }


    for (var i=0; i < 6; i++){

      var background = "rgba(255, 215, 0, " + i * 0.2 + ")";

      var toAdd = "<div class='' style='background-color:" + background + "'>" +
      (50 + i * increment).toFixed(1) + "%";

      if (i == 5){
        toAdd += "+"
      }
      toAdd += "</div>";

      $("#keyonmap").append(toAdd);
    }
      $("#keyonmap").append("<div class='remain'>Remain</div>")
  }


};
;var partylist = {};

partylist["labour"] = "Labour";
partylist["conservative"] = "Conservative";
partylist["libdems"] = "Lib Dems";
partylist["snp"] = "SNP";
partylist["ukip"] = "UKIP";
partylist["plaidcymru"] = "Plaid Cymru";
partylist["green"] = "Green";
partylist["uu"] = "UUP";
partylist["sinnfein"] = "Sinn Féin";
partylist["sdlp"] = "SDLP";
partylist["dup"] = "DUP";
partylist["alliance"] = "Alliance";
partylist["other"] = "Other";
partylist["others"] = "Others";

partylist["leave"] = "Leave";
partylist["remain"] = "Remain";


var regionlist = {};

regionlist["london"] = "London";
regionlist["southeastengland"] = "S.E. England";
regionlist["southwestengland"] = "S. W. England";
regionlist["westmidlands"] = "W. Midlands";
regionlist["northwestengland"] = "N. W. England";
regionlist["northeastengland"] = "N. E. England";
regionlist["yorkshireandthehumber"] = "Yorks & Humber";
regionlist["eastmidlands"] = "E. Midlands";
regionlist["eastofengland"] = "E. England";
regionlist["scotland"] = "Scotland";
regionlist["wales"] = "Wales";
regionlist["northernireland"] = "N. Ireland";
regionlist["unitedkingdom"] = "United Kingdom";
regionlist["greatbritain"] = "Great Britain"
regionlist["england"] = "England";

var regionMap = {
  "unitedkingdom" : ["northeastengland", "northwestengland", "yorkshireandthehumber", "southeastengland",
                "southwestengland", "eastofengland", "eastmidlands", "westmidlands", "london",
              "scotland", "wales", "northernireland"],

  "greatbritain" : ["northeastengland", "northwestengland", "yorkshireandthehumber", "southeastengland",
                "southwestengland", "eastofengland", "eastmidlands", "westmidlands", "london",
              "scotland", "wales"],

  "england"  : ["northeastengland", "northwestengland", "yorkshireandthehumber", "southeastengland",
                "southwestengland", "eastofengland", "eastmidlands", "westmidlands", "london"]

};
;var filters = {
  state : {
    "leave" : [0, 100]
  },

  selectHandle : function(parameter, criteria){

    parameter = parameter.substring(7); // trim filter- off of parameters


    if (criteria == "null"){
      delete filters.state[parameter]
    } else {
      if (parameter == "region" && criteria == "england"){
        filters.state["region"] = regionMap["england"]
      } else {
        filters.state[parameter] = criteria;
      }
    }

    filters.filter();
  },

  leave : function(parameter, value){
    if (parameter == "low"){
      filters.state.leave[0] = value;
    } else if (parameter == "high"){
      filters.state.leave[1] = value;
    }
    filters.filter()
  },

  opacities : {},

  filter : function(){

    $.each(currentMap.seatData, function(seat, data){
      var meetsCriteria = true


      $.each(filters.state, function(parameter, criteria){

        if (parameter == "leave"){
          var leave = data.leave;

          if (leave < filters.state["leave"][0] || leave > filters.state["leave"][1] ){
            meetsCriteria = false;
          }

        }  else if (parameter == "current") {
          if (data.current != criteria){
            meetsCriteria = false;
          }

        } else {

          //if (data.seatInfo[parameter] != criteria){ // TO DO CHECK NOT BROKEN
          if (criteria.indexOf(data[parameter]) == - 1){
            meetsCriteria = false;
          }
        }
      });

      if (meetsCriteria == true){
        var current = currentMap.seatData[seat]["current"];
        filters.opacities[seat] = 10 * ((currentMap.seatData[seat][current] / 100) - 0.5) ;
        data.filtered = true;
      } else {
        filters.opacities[seat] = 0.03;
        data.filtered = false;
      }
    })

    filters.display();

  },

  display: function(){
    $.each(filters.opacities, function(seat, opacity){

      var mapSelect = currentMap.seatData[seat].mapSelect;
      mapSelect.opacity = opacity;
      d3.select("#i" + mapSelect.properties.info_id).attr("opacity", opacity)
    });

    // for table
    filters.filteredList = [];
    $.each(currentMap.seatData, function(seat, data){
      if (data.filtered == true){
        currentMap.seatData[seat]["name"] = seat
        filters.filteredList.push(currentMap.seatData[seat]);

      }
    });

    $("#seatlist-sort" + voteTotals.activeSort).removeClass("sort-active");
    voteTotals.activeSort = "leave";
    $("#seatlist-sortleave").addClass("sort-active");

    voteTotals.display(filters.filteredList);
  },

  reset : function(){
    filters.state = {"leave" : [0, 100]}; // reset filters state
    // reset ui
    $("#filter-current option:eq(0)").prop("selected", true);
    $("#filter-winner2015 option:eq(0)").prop("selected", true);
  	$("#filter-region option:eq(0)").prop("selected", true);
  	$("#filter-leave").get(0).reset();

    // reset map
    filters.filter();
  },

  filteredList : []



}
;var mapAttr = {
  "width" : 600,
  "height" : 875,
  "active" : d3.select(null),

	// nodes for various  map ui changes (flash seat/choropleths etc)
	"activeNode" : null,
	//when map clicked on function
	clicked : function(d){

		mapAttr.activeNode = d;

		// flash seat on click
		mapAttr.flashSeat(d);

		var bounds = path.bounds(d),
			dx = Math.pow((bounds[1][0] - bounds[0][0]), 0.5),
			dy = Math.pow((bounds[1][1] - bounds[0][1]), 0.5),
			x = (bounds[0][0] + bounds[1][0]) / 2,
			y = (bounds[0][1] + bounds[1][1]) / 2,

			scale = .05 / Math.max(dx /  mapAttr.width,  dy /  mapAttr.height),
			translate = [ mapAttr.width / 2 - scale * x,  mapAttr.height / 2 - scale * y];

		mapAttr.disableZoom();

		svg.transition()
				.duration(1500)
				.call(zoom.transform, d3.zoomIdentity.translate(translate[0],translate[1]).scale(scale))
				.on("end", mapAttr.enableZoom);

    uiAttr.showDiv("seat-information")
		seatInfoTable.display(d.properties.name);
	},

	// flash seat on click - reset previous
	flashSeat: function(node){

		var mapID = "#i" + node.properties.info_id;
		current = d3.select(mapID);

		//var opacity = $(mapID).css("opacity");

		repeat();

		function repeat(){
			current
				.transition()
					.duration(1500)
					.attr("opacity", 0.02)
				.transition()
					.duration(1500)
					.attr("opacity", node.opacity)
				.on("end", repeat);
		}

	},

	//stops users messing up zoom animations
	disableZoom : function(){

		svg.on("mousedown.zoom", null);
		svg.on("mousemove.zoom", null);
		svg.on("dblclick.zoom", null);
		svg.on("touchstart.zoom", null);
		svg.on("wheel.zoom", null);
		svg.on("mousewheel.zoom", null);
		svg.on("MozMousePixelScroll.zoom", null);

	},

	// reenables users ability to zoom pan etc
	enableZoom : function(){
		svg.call(zoom);
	},

	// reset button for map
	reset : function() {

		//stop previous seat flashing
		if (mapAttr.activeNode != null){
			d3.select("#i" + mapAttr.activeNode.properties.info_id)
			.transition()
			.attr("opacity", mapAttr.activeNode.opacity);
		}

		mapAttr.activeNode = null;

		mapAttr.disableZoom();

		svg.transition()
			.duration(1500)
			.call(zoom.transform, d3.zoomIdentity)
			.on("end", function(){mapAttr.enableZoom()});
	},

	// zoom function
	zoomed : function() {
		g.style("stroke-width", 1.5 / d3.event.scale + "px");
		g.attr("transform", d3.event.transform);
	},

	stopped : function() {
		if (d3.event.defaultPrevented) {
			d3.event.stopPropagation();
		}
	},

	// load + colour map at page load
	loadmap : function(setting){
		// d3.json(setting.mapurl, function(error, uk) {
		// 	if (error) return console.error(error);

			g.selectAll(".map")
				.data(topojson.feature(setting.polygons, setting.polygons.objects.map).features)
				.enter().append("path")
				.attr("class", function(d){
					var seatClass;
					if (d.properties.name in setting.seatData){
						 seatClass = "map " + setting.seatData[d.properties.name]["current"];
					} else {
						seatClass = "map null"
					}
					return seatClass
				})
				.attr("opacity", function(d) {
					if (d.properties.name in currentMap.seatData){
						var current = setting.seatData[d.properties.name]["current"];
						var opacity = 10 * ((setting.seatData[d.properties.name][current] / 100) - 0.5) ;

						// for finding seat from various search features
						setting.seatData[d.properties.name]["mapSelect"] = d;
						// set current opacity - for flashseat and choropleths //
						setting.seatData[d.properties.name]["mapSelect"]["opacity"]	= opacity;
						// set opacity = 1 for filters
						filters.opacities[d.properties.name] = 1;
						return opacity
					}
					else {
						return 0;
					}

				})
				.attr("id", function(d) {
					return "i" + d.properties.info_id;
					})
				.attr("d", path)
				.on("click", mapAttr.clicked)
				.append("svg:title")
					.text(function(d) { return d.properties.name})
				.each(function(d){
					if (d.properties.name in currentMap.seatData){
						// lset filtreed to true
						setting.seatData[d.properties.name].filtered = true;

					}
				});

			g.append("path")
				.datum(topojson.mesh(setting.polygons, setting.polygons.objects.map, function(a, b){
					return a.properties.region != b.properties.region && a != b; }))
				.attr("d", path)
				.attr("class", "boundaries");
		// });
	}

}

var projection = d3.geoAlbers() // variables to change default map position
	.center([-0.5, 54.4]) //centers vertically and horizontally
	.rotate([2.25, 0])
	.parallels([51, 60])
	.scale(5300)
	.translate([mapAttr.width / 2, mapAttr.height / 2])


var path = d3.geoPath().projection(projection);

var zoom = d3.zoom()
  .scaleExtent([1, 8])
  .on("zoom", mapAttr.zoomed);
;function getData(setting){

	$.when(
		$.getJSON(setting.mapurl, function(data){
			setting.polygons = data;
		}),
		$.getJSON(setting.dataurl, function(data){
			setting.seatData = data;
			currentMap = setting;
			//mapAttr.loadmap(currentMap);
		})

	).then(function(){
		pageLoadEssentials();
	})
};

function pageLoadEssentials(){
	// function does all the random crap that needs changing / resetting on pageload

	// laod the map
	mapAttr.loadmap(currentMap);
	// reset filters.state - also get and show vote totals
	filters.opacities = {};
	filters.reset();

	//key on map
	choro.keyOnMap();

	// populate seat search	// autocomplete function
	var seatList = Object.keys(currentMap.seatData);
	seatList.sort();

	$(function(){
		$("#searchseats").autocomplete({
			source: seatList,
			select: function(event, ui){
				searchSeats(ui.item.label);
			}
		});
	});


	//show and hide various divs on load
	uiAttr.pageLoadDiv();

	// firefox css nonsense
	if(navigator.userAgent.toLowerCase().indexOf('firefox') > -1){
		$("div").css("font-size", "98%");
		$("select").css("font-size", "98%")
		// check this TO DO
	}

};

// various page interaction functions
function searchSeats(value){
	mapAttr.clicked(currentMap.seatData[value].mapSelect);
};


function pageSetting(name, mapurl, dataurl, previous, election, predict){

	this.name = name;
	this.mapurl = mapurl;
	this.dataurl = dataurl;
	this.previous = previous;
	this.election = election;
	this.predict = predict;

	this.seatData = {};
	this.polygons = {};
}

var currentMap;

var dataurls =  {
	// maps
	map650 : "650seat_no_ni.json",


	//parliaments
	brexit : "houseofcommons/brexit.json"

}

var brexit = new pageSetting("brexit", dataurls.map650, dataurls.brexit, dataurls.brexit, false, false);

function initialization(){

	var setting = brexit;

	uiAttr.changeNavBar(setting.name);

	$(document).ready(function(){
		getData(setting);
	});
}
;var seatInfoTable = {
  // previous display state of table
  party : null,

  display : function(seat){

    var activeSeat = currentMap.seatData[seat];

    var votes =  [
      {"party" : "remain", "percent" : activeSeat.remain},
      {"party" : "leave", "percent" : activeSeat.leave}
    ];

    votes.sort(function(a, b){
      return b.percent - a.percent;
    });

    seatInfoTable.seatInfo(seat, activeSeat); // draw tables with data
    seatInfoTable.voteTable(votes); // draw table - do first since votes altered
    seatInfoTable.barChart(votes); // draw bar chart with data
  },

  seatInfo : function(seat, data){

    // clean old divs
    $("#information-party .party-flair").removeClass(seatInfoTable.party);


    $("#information-seatname-span").text(seat);
    $("#information-region").text(regionlist[data.region]);

    $("#information-party .party-name").text(partylist[data.winner2015]);
    $("#information-party .party-flair").addClass(data.winner2015);

    $("#information-majority").text("");

    $("#information-turnout").text("");

    // set for next time
    seatInfoTable.party = data.winner2015;

  },

  barChart : function(votes){
    $("#information-bar").empty();


    var dataitems = votes.length;
    var margin = {top: 10, right: 0, bottom: 10, left: 25};

  	var width = 200 - margin.left - margin.right;
  	var height = 200 - margin.top - margin.bottom;
  	var bargap = 2;
  	var barwidth = d3.min([90, (width / dataitems) - bargap]);
  	var animationdelay = 250;

    var svg1 = d3.select("#information-bar")
    		.append("svg")
    			.attr("width", width + margin.left + margin.right)
    			.attr("height", height + margin.top + margin.bottom)
    		.append("g")
        	.attr("transform", "translate(" + margin.left + "," + margin.top + ")");

  	var x = d3.scaleOrdinal().range([0, dataitems * (barwidth + 2)]);

  	var xAxis = d3.axisBottom()
  	    .scale(x);

  	var y = d3.scaleLinear()
  		.range([height, 0]);

  	var yAxis = d3.axisLeft()
      .scale(y)
      .ticks(6);

  	var max_of_votes = d3.max(votes, function(d) { return d.percent / 1 ; });

  	y.domain([0, d3.max([60, (max_of_votes + (10 - max_of_votes % 10))])]);

    svg1.append("g")
      .attr("class", "x axis")
      .attr("transform", "translate(0," + height + ")")
      .call(xAxis);

  	svg1.append("g")
      .attr("class", "y axis")
      .call(yAxis);

  	svg1.selectAll("rect")
  		.data(votes)
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
        .attr("y", function(d) { return y(d.percent); })
        .attr("height", function(d) { return height - y(d.percent ); });
  },

  voteTable : function(votes){
    $("#information-chart").empty();

    // see if any changes
    for (var i=0; i < votes.length; i++){

      // add divs - styled in electionmap.scss
      var toAdd = "<div class='party-info-table'>";
      // add party-block
      toAdd += "<div class='party-flair " + votes[i].party + "'></div>"
      // add mp name
      toAdd += "<div>" + partylist[votes[i].party] + "</div>"

      toAdd += "<div>" + votes[i].percent.toFixed(2) + "%</div>";

      toAdd += "</div>";

      $("#information-chart").append(toAdd);
    }
  }
};
;var uiAttr = {
  changeNavBar : function(elem){
    $(".navbaractive").removeClass("navbaractive");

    var id = "#nav-" + elem;

    $(id).addClass("navbaractive");
      // change pagetitle;
    var text = $(id).text();

    $("#pagetitle").html(text);
    document.title = text;
  },

  clickMapButton : function(div){
    var divClass = $(div).attr("class");
    var divID = $(div).attr("id");

    if (divClass.indexOf("mapbuttonactive") == -1){

      $(div).addClass("mapbuttonactive");
      uiAttr.showDiv(divID);
    } else {
      uiAttr.hideDiv(divID);
    }
  },

  showDiv: function(id){

    // check z indexes
    uiAttr.zIndexCheck(id)

    // make div visible and draggable by h2
    // on mousedown change z-index - bring to front
    $(uiAttr.buttonToDiv[id]).show();
    $(uiAttr.buttonToDiv[id]).mousedown(function(){
      uiAttr.zIndexCheck(id);
      uiAttr.zIndexShuffle();
    }).draggable({
      handle: "h2",
      containment: "#wrapper"
    });

    //alter z index
    uiAttr.zIndexShuffle();
  },

  hideDiv: function(id){

    var i = uiAttr.zIndexTracker.indexOf(id);
    $("#" + id).removeClass("mapbuttonactive");

    if (i != -1){
      uiAttr.zIndexTracker.splice(i, 1);
    }

    $(uiAttr.buttonToDiv[id]).hide();
    uiAttr.zIndexShuffle();
  },

  // map buttons to divs
  buttonToDiv : {
    "brexitvotesbutton" : "#brexitvotes",
    "seat-information" : "#seat-information",
    "votetotalsbutton" : "#votetotals",
    "filtersbutton" : "#filters",
    "choroplethsbutton" : "#choropleths",
    "seatlistbutton" : "#seatlist",
    "seatlist-extend" : "#seatlist-extended",
    "predictbutton" : "#userinput",
    "seat-600" : "#seat-600"
  },

  //store  and reorder z indexes of hidden divs
  zIndexTracker : [],

  zIndexCheck : function(id){
    if (uiAttr.zIndexTracker.indexOf(id) == -1 ){
      uiAttr.zIndexTracker.push(id);

    } else {
      var fromIndex = uiAttr.zIndexTracker.indexOf(id);
      var toIndex = uiAttr.zIndexTracker.length - 1;

      uiAttr.zIndexTracker.splice(fromIndex, 1);
      uiAttr.zIndexTracker.splice(toIndex, 0, id);
    }
  },

  zIndexShuffle: function(){

    var zIndices =  uiAttr.zIndexTracker;

    for (var i = 0; i < zIndices.length; i++){
      $(uiAttr.buttonToDiv[zIndices[i]]).css("z-index", i + 1);
    }
  },

  pageLoadDiv : function(){
    $.each(uiAttr.buttonToDiv, function(button, div){
      if (button == "brexitvotesbutton" || button == "filtersbutton" ){
        uiAttr.showDiv(button);
      } else if (button == "predictbutton" && currentMap.predict == true){
        $("#predictbutton").removeClass("hidden").addClass("mapbuttonactive");
        uiAttr.showDiv(button);
      } else {
        uiAttr.hideDiv(button);
      }
    });

  }
}
;voteTotals = {
  display : function(seatlist){
    voteTotals.sortState = {
        "name" : "asc",
        "current" : "asc",
        "winner2015" : "asc",
        "leave" : "desc",
        "remain" : "desc"
    };

    voteTotals.reorder("leave", "desc");


  },

  activeSort : "leave",

  sortState : {},

  tableSort: function(parameter){
    parameter = parameter.slice(13);

    //remove from old table header
    $("#seatlist-sort" + voteTotals.activeSort).removeClass("sort-active")
    voteTotals.activeSort = parameter;

    $("#seatlist-sort" + voteTotals.activeSort).addClass("sort-active");

    voteTotals.reorder(parameter, voteTotals.sortState[parameter]);

    if (voteTotals.sortState[parameter] == "desc"){
      voteTotals.sortState[parameter] = "asc";
    } else if (voteTotals.sortState[parameter] == "asc"){
      voteTotals.sortState[parameter] = "desc";
    }
  },

  reorder : function(parameter, sorttype){
    // sort works both alphabetically and numerically
    if (sorttype == "asc"){
      filters.filteredList.sort(function(a, b){
        if (a[parameter] < b[parameter]){
          return -1;
        }
        if (a[parameter] > b[parameter]){
          return 1;
        }
        return 0
      })
    }
    if (sorttype == "desc"){
      filters.filteredList.sort(function(a, b){
        if (a[parameter] < b[parameter]){
          return 1;
        }
        if (a[parameter] > b[parameter]){
          return -1;
        }
        return 0;
      })
    }

    voteTotals.extendedList();
  },

  seatTotals: function(){
      $("#brexitvotes-seatnumbers").empty();
      var totalSeats = filters.filteredList.length;
      var remainSeats = 0;
      var leaveSeats = 0;

      $.each(filters.filteredList, function(seat, data){
        if (data.current == "leave"){
          leaveSeats += 1;
        } else {
          remainSeats += 1;
        }

      });

      var toAdd = "<p>Total : " + totalSeats + " Remain: " + remainSeats + " Leave: " + leaveSeats + "</p>"


      $("#brexitvotes-seatnumbers").append(toAdd);

  },

  extendedList : function(){
    voteTotals.seatTotals();

    $("#brexitvotes-data").empty();

    $.each(filters.filteredList, function(i, seat){

      var seatName = seat.name;

      var party = "<div style='margin-left: 10px' class='party-flair " + seat.current + "'></div>";
      var winner2015 = "<div style='margin-left: 10px' class='party-flair " + seat.winner2015 + "'></div>"
      var leave = "<div style='margin-left: 10px'>" + seat.leave.toFixed(2) + "</div>"
      var remain = "<div style='margin-left: 10px'>" + seat.remain.toFixed(2) + "</div>"
      var divider = "</div><div>";

      var toAdd = "<div><div class='extended-seat' onclick='mapAttr.clicked(currentMap.seatData[\""
      + seat.name + "\"].mapSelect)'>" + seatName + divider + party + divider + winner2015 + divider + leave + divider
      + remain + "</div></div>";

      $("#brexitvotes-data").append(toAdd);

    });
  }

};
